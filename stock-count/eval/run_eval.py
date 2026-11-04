#!/usr/bin/env python3
"""階段 0:評測拍照辨識計數的準確度。

用法(先把照片與人工計數準備好,格式見 README.md):
  # 方式 A:直接呼叫 Claude(部署前即可測;需環境變數 ANTHROPIC_API_KEY)
  python3 run_eval.py --photos photos --truth ground_truth.csv --products products.csv --direct

  # 方式 B:呼叫已部署的 Edge Function(測的就是正式環境)
  python3 run_eval.py ... --endpoint https://xxxx.supabase.co/functions/v1/count-photo --key <anon key>

  # 自我測試(不連網路、不需照片)
  python3 run_eval.py ... --mock

只用標準函式庫;若有安裝 Pillow 會自動縮圖(與網頁行為一致)。
"""
import argparse
import base64
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EDGE_TS = HERE.parent / "supabase" / "functions" / "count-photo" / "index.ts"


# ---------- 讀資料 ----------
def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


def load_products(path):
    prods = {}
    for r in read_csv(path):
        code = r.get("barcode", "")
        if code:
            prods[code] = r
    return prods


def load_truth(path):
    """回傳 {photo: {barcode: count}};照片中沒列出的商品視為 0。"""
    truth = defaultdict(dict)
    for r in read_csv(path):
        photo, code = r.get("photo", ""), r.get("barcode", "")
        if not photo or not code:
            continue
        try:
            truth[photo][code] = truth[photo].get(code, 0) + int(float(r.get("count", "0") or 0))
        except ValueError:
            sys.exit(f"人工計數不是數字:{r}")
    return truth


# ---------- 提示詞(直接從 Edge Function 原始碼取出,避免兩邊不一致) ----------
def build_prompt(products):
    src = EDGE_TS.read_text(encoding="utf-8")
    m = re.search(r"const prompt = `(.*?)`;", src, re.S)
    if not m:
        sys.exit("無法從 index.ts 取出提示詞")
    plist = "\n".join(f"{c} | {p.get('name', '')}" for c, p in list(products.items())[:500])
    return m.group(1).replace('${list || "(無)"}', plist or "(無)")


# ---------- 圖片 ----------
def image_b64(path, max_side=1600):
    data = Path(path).read_bytes()
    try:
        from PIL import Image  # 選用
        img = Image.open(io.BytesIO(data)).convert("RGB")
        s = min(1.0, max_side / max(img.size))
        if s < 1.0:
            img = img.resize((int(img.width * s), int(img.height * s)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        data = buf.getvalue()
    except ImportError:
        if len(data) > 5_000_000:
            sys.exit(f"{path} 超過 5MB,請先縮圖或安裝 Pillow(pip install pillow)")
    return base64.b64encode(data).decode()


# ---------- 辨識 ----------
def post_json(url, body, headers, timeout=120):
    req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def parse_items(text):
    m = re.search(r"\{[\s\S]*\}", text or "")
    return json.loads(m.group(0)).get("items", []) if m else []


def recognize(args, photo_path, products):
    if args.direct:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            sys.exit("--direct 需要環境變數 ANTHROPIC_API_KEY")
        body = {
            "model": args.model, "max_tokens": 2000,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                              "data": image_b64(photo_path)}},
                {"type": "text", "text": build_prompt(products)}]}],
        }
        res = post_json("https://api.anthropic.com/v1/messages", body,
                        {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        text = next((c["text"] for c in res.get("content", []) if c.get("type") == "text"), "")
        return parse_items(text)
    h = {"Content-Type": "application/json"}
    if args.key:
        h["Authorization"] = "Bearer " + args.key
        h["apikey"] = args.key
    body = {"image": image_b64(photo_path), "media_type": "image/jpeg",
            "products": [{"code": c, "name": p.get("name", "")} for c, p in products.items()]}
    return post_json(args.endpoint, body, h).get("items", [])


def recognize_retry(args, photo_path, products, tries=3):
    for i in range(tries):
        try:
            return recognize(args, photo_path, products)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            print(f"  ! {photo_path} 第 {i + 1} 次失敗:{e}", file=sys.stderr)
            time.sleep(2 ** i)
    return None


def mock_items(truth_counts):
    """自我測試用:對人工計數加上固定的小誤差。"""
    out = []
    for i, (code, n) in enumerate(sorted(truth_counts.items())):
        if i % 3 == 1:
            n += 1
        elif i % 3 == 2:
            n = max(0, n - 2)
        out.append({"code": code, "name": code, "count": n, "confidence": ["high", "medium", "low"][i % 3], "note": ""})
    out.append({"code": None, "name": "未知餅乾", "count": 2, "confidence": "low", "note": "mock"})
    return out


# ---------- 比對 ----------
RANK = {"high": 0, "medium": 1, "low": 2}


def compare(photo, truth_counts, items, products):
    pred, conf, unknown = defaultdict(int), {}, []
    for it in items:
        try:
            n = max(0, int(float(it.get("count", 0))))
        except (TypeError, ValueError):
            n = 0
        code = it.get("code")
        if not code:
            unknown.append((it.get("name", "?"), n))
            continue
        pred[code] += n
        c = it.get("confidence", "medium")
        if code not in conf or RANK.get(c, 1) > RANK.get(conf[code], 1):
            conf[code] = c  # 同商品多筆時取最低信心
    rows = []
    for code in sorted(set(truth_counts) | set(pred)):
        t, p = truth_counts.get(code, 0), pred.get(code, 0)
        rows.append({"photo": photo, "barcode": code,
                     "name": products.get(code, {}).get("name", "(不在主檔)"),
                     "truth": t, "pred": p, "err": p - t,
                     "conf": conf.get(code, "(未偵測)"),
                     "in_master": code in products})
    return rows, unknown


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def summarize(rows, unknown_all, target):
    n = len(rows)
    exact = sum(r["err"] == 0 for r in rows)
    w1 = sum(abs(r["err"]) <= 1 for r in rows)
    w10 = sum(abs(r["err"]) <= max(0.1 * r["truth"], 0) for r in rows)
    mae = sum(abs(r["err"]) for r in rows) / n if n else 0
    present = [r for r in rows if r["truth"] > 0]
    found = sum(r["pred"] > 0 for r in present)
    claimed = [r for r in rows if r["pred"] > 0]
    correct_claim = sum(r["truth"] > 0 for r in claimed)
    halluc = [r for r in rows if r["pred"] > 0 and not r["in_master"]]

    print("\n===== 整體結果 =====")
    print(f"比對筆數(照片×商品):{n}")
    print(f"完全正確:{pct(exact, n)}   誤差 ±1 以內:{pct(w1, n)}   誤差 10% 以內:{pct(w10, n)}   平均絕對誤差:{mae:.2f}")
    print(f"商品辨識召回(真的有的有認出):{pct(found, len(present))}   精確(認出的真的有):{pct(correct_claim, len(claimed))}")
    print(f"回報了主檔以外的條碼(幻覺):{len(halluc)} 筆   無法對應主檔的未知商品:{len(unknown_all)} 筆")

    by_photo = defaultdict(lambda: [0, 0])
    for r in rows:
        by_photo[r["photo"]][0] += r["truth"]
        by_photo[r["photo"]][1] += r["pred"]
    print("\n--- 每張照片總件數(人工 / AI) ---")
    for ph, (t, p) in sorted(by_photo.items()):
        print(f"  {ph}: {t} / {p}  ({p - t:+d})")

    print("\n--- 信心度是否可信(信心越高應該越準) ---")
    groups = defaultdict(list)
    for r in rows:
        groups[r["conf"]].append(r)
    for c in ["high", "medium", "low", "(未偵測)"]:
        g = groups.get(c, [])
        if g:
            print(f"  {c:8s} {len(g):4d} 筆,完全正確 {pct(sum(x['err'] == 0 for x in g), len(g))}")

    print("\n--- 各商品(誤差由大到小) ---")
    per = defaultdict(lambda: {"n": 0, "abs": 0, "bias": 0, "name": ""})
    for r in rows:
        d = per[r["barcode"]]
        d["n"] += 1
        d["abs"] += abs(r["err"])
        d["bias"] += r["err"]
        d["name"] = r["name"]
    for code, d in sorted(per.items(), key=lambda kv: -kv[1]["abs"])[:15]:
        print(f"  {code} {d['name'][:16]:16s} 筆數 {d['n']:3d}  平均絕對誤差 {d['abs'] / d['n']:.2f}  偏差 {d['bias'] / d['n']:+.2f}")

    # 混淆候選:同一張照片裡一個商品少算、另一個多算
    conf_pairs = defaultdict(float)
    byp = defaultdict(list)
    for r in rows:
        byp[r["photo"]].append(r)
    for rs in byp.values():
        for a in (x for x in rs if x["err"] < 0):
            for b in (x for x in rs if x["err"] > 0):
                conf_pairs[(a["name"], b["name"])] += min(-a["err"], b["err"])
    if conf_pairs:
        print("\n--- 疑似混淆(真實是左邊,被算成右邊) ---")
        for (a, b), w in sorted(conf_pairs.items(), key=lambda kv: -kv[1])[:5]:
            print(f"  {a} → {b}  累計 {w:.0f} 件")

    print("\n--- 誤差最大的 10 筆 ---")
    for r in sorted(rows, key=lambda r: -abs(r["err"]))[:10]:
        print(f"  {r['photo']} {r['name'][:14]:14s} 人工 {r['truth']:3d} AI {r['pred']:3d} ({r['err']:+d}) 信心 {r['conf']}")

    ok = n and w10 / n >= target
    print(f"\n目標:誤差 10% 以內 ≥ {target:.0%} → {'達標 ✓' if ok else '未達標 ✗'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--photos", default="photos", help="照片資料夾")
    ap.add_argument("--truth", default="ground_truth.csv")
    ap.add_argument("--products", default="products.csv")
    ap.add_argument("--out", default="results", help="輸出檔名前綴")
    ap.add_argument("--direct", action="store_true", help="直接呼叫 Claude(需 ANTHROPIC_API_KEY)")
    ap.add_argument("--endpoint", help="已部署的 count-photo 函式網址")
    ap.add_argument("--key", help="Supabase anon key(選填)")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--mock", action="store_true", help="自我測試,不連網路")
    ap.add_argument("--target", type=float, default=0.9, help="『誤差 10% 以內』的目標比例")
    args = ap.parse_args()
    if not (args.direct or args.endpoint or args.mock):
        ap.error("請指定 --direct、--endpoint 或 --mock 其中之一")

    products, truth = load_products(args.products), load_truth(args.truth)
    if not truth:
        sys.exit("ground_truth.csv 沒有資料")
    rows, unknown_all, failed = [], [], []
    for i, (photo, counts) in enumerate(sorted(truth.items()), 1):
        print(f"[{i}/{len(truth)}] {photo}")
        if args.mock:
            items = mock_items(counts)
        else:
            path = Path(args.photos) / photo
            if not path.exists():
                print(f"  ! 找不到照片 {path},略過", file=sys.stderr)
                failed.append(photo)
                continue
            items = recognize_retry(args, path, products)
            if items is None:
                failed.append(photo)
                continue
        r, u = compare(photo, counts, items, products)
        rows += r
        unknown_all += [(photo, *x) for x in u]

    if not rows:
        sys.exit("沒有任何可比對的結果")
    with open(f"{args.out}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["photo", "barcode", "name", "truth", "pred", "err", "conf", "in_master"])
        w.writeheader()
        w.writerows(rows)
    with open(f"{args.out}_unknown.json", "w", encoding="utf-8") as f:
        json.dump(unknown_all, f, ensure_ascii=False, indent=1)
    summarize(rows, unknown_all, args.target)
    if failed:
        print(f"\n失敗或略過的照片:{', '.join(failed)}")
    print(f"\n明細已存:{args.out}.csv、{args.out}_unknown.json")


if __name__ == "__main__":
    main()
