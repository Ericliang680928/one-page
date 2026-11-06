#!/usr/bin/env python3
"""階段 0:評測拍照辨識計數的準確度。

用法(先把照片與人工計數準備好,格式見 README.md):
  # 方式 A:直接呼叫 Claude(部署前即可測;需環境變數 ANTHROPIC_API_KEY)
  python3 run_eval.py --photos photos --truth ground_truth.csv --products products.csv --direct

  # 方式 B:呼叫已部署的 Edge Function(測的就是正式環境)
  python3 run_eval.py ... --endpoint https://xxxx.supabase.co/functions/v1/count-photo --key <anon key>

  # 自我測試(不連網路、不需照片)
  python3 run_eval.py ... --mock

--direct 需要 `pip install anthropic`;建議再裝 Pillow 以自動縮圖(與網頁行為一致)。
其餘模式只用標準函式庫。
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
EDGE_TS = HERE.parent / "supabase" / "functions" / "_shared" / "count.ts"


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
def load_prompts():
    src = EDGE_TS.read_text(encoding="utf-8")
    r = re.search(r"const RULES_TEMPLATE = `(.*?)`;", src, re.S)
    u = re.search(r"const USER_TEXT = `(.*?)`;", src, re.S)
    h = re.search(r"const HINT_HEADER = `(.*?)`;", src, re.S)
    if not (r and u and h):
        sys.exit("無法從 _shared/count.ts 取出提示詞(RULES_TEMPLATE / USER_TEXT / HINT_HEADER)")
    return r.group(1), u.group(1), h.group(1)


def build_user_text(hints):
    """與 Edge Function 相同:最多 15 條、每條 200 字、去除換行。"""
    _, user, header = load_prompts()
    lines = [re.sub(r"\s+", " ", str(h))[:200] for h in (hints or [])[:15]]
    lines = [x for x in lines if x]
    return user + "\n\n" + header + "\n" + "\n".join("- " + x for x in lines) if lines else user


def build_rules(products):
    """與 Edge Function 相同:商品清單依條碼排序,確保前綴固定、可命中快取。"""
    tpl = load_prompts()[0]
    items = sorted(products.items(), key=lambda kv: kv[0])[:500]
    plist = "\n".join(f"{c} | {p.get('name', '')}" for c, p in items)
    return tpl.replace("{{LIST}}", plist or "(無)")


# ---------- 圖片 ----------
def image_b64(path, max_side=1600):  # 可用 --max-side 覆寫
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
    """回傳 (items, usage);usage 為 dict(input, cache_write, cache_read, output) 或 None。"""
    b64 = image_b64(photo_path, args.max_side)
    if args.direct:
        try:
            import anthropic
        except ImportError:
            sys.exit("--direct 需要安裝 SDK:pip install anthropic")
        client = anthropic.Anthropic()  # 讀取 ANTHROPIC_API_KEY
        user_text = build_user_text(args.hints_list)
        msg = client.messages.create(
            model=args.model, max_tokens=16000,
            output_config={"effort": args.effort},
            system=[{"type": "text", "text": build_rules(products),
                     "cache_control": {"type": "ephemeral", "ttl": args.cache_ttl}}],
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": user_text}]}],
        )
        u = msg.usage
        usage = {"input": u.input_tokens, "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
                 "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0, "output": u.output_tokens}
        if msg.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"stop_reason={msg.stop_reason}")
        text = next((c.text for c in msg.content if c.type == "text"), "")
        return parse_items(text), usage
    h = {"Content-Type": "application/json"}
    if args.key:
        h["Authorization"] = "Bearer " + args.key
        h["apikey"] = args.key
    body = {"image": b64, "media_type": "image/jpeg",
            "products": [{"code": c, "name": p.get("name", "")} for c, p in products.items()],
            "hints": args.hints_list}
    res = post_json(args.endpoint, body, h)
    return res.get("items", []), res.get("usage")


def recognize_retry(args, photo_path, products, tries=3):
    for i in range(tries):
        try:
            return recognize(args, photo_path, products)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, RuntimeError, OSError) as e:
            print(f"  ! {photo_path} 第 {i + 1} 次失敗:{e}", file=sys.stderr)
            time.sleep(2 ** i)
    return None, None


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


def summarize(rows, unknown_all, target, usages=None, args=None):
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

    if usages:
        k = len(usages)
        tot = {f: sum(u[f] for u in usages) for f in ("input", "cache_write", "cache_read", "output")}
        print(f"\n--- Token 用量與成本(共 {k} 次呼叫) ---")
        print(f"  平均每張:未快取輸入 {tot['input'] / k:.0f}、寫入快取 {tot['cache_write'] / k:.0f}、"
              f"讀取快取 {tot['cache_read'] / k:.0f}、輸出(含思考) {tot['output'] / k:.0f}")
        inp_all = tot["input"] + tot["cache_write"] + tot["cache_read"]
        print(f"  快取命中率(讀取 / 全部輸入):{pct(tot['cache_read'], inp_all)}")
        if args:
            cost = (tot["input"] * args.price_in + tot["cache_write"] * args.price_in * 1.25
                    + tot["cache_read"] * args.price_in * 0.1 + tot["output"] * args.price_out) / 1e6
            nocache = (inp_all * args.price_in + tot["output"] * args.price_out) / 1e6
            print(f"  估算成本:共 ${cost:.4f}(每張 ${cost / k:.4f});若完全沒有快取約 ${nocache:.4f}")
            print(f"  (單價 輸入 ${args.price_in}/百萬、輸出 ${args.price_out}/百萬;寫入快取 1.25 倍、讀取 0.1 倍。請以官方最新價格為準)")

    ok = n and w10 / n >= target
    print(f"\n目標:誤差 10% 以內 ≥ {target:.0%} → {'達標 ✓' if ok else '未達標 ✗'}")


def load_hints(path):
    if not path:
        return []
    text = Path(path).read_text(encoding="utf-8").strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) for x in data]
    except json.JSONDecodeError:
        pass
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


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
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"],
                    help="思考深度,影響輸出 token(與 Edge Function 的 COUNT_EFFORT 對應)")
    ap.add_argument("--cache-ttl", default="5m", choices=["5m", "1h"])
    ap.add_argument("--max-side", type=int, default=1600, help="照片長邊像素,越小越省 token")
    ap.add_argument("--hints", help="學習提示檔(JSON 字串陣列,或每行一條的文字檔),用來比較有無提示的準確度")
    ap.add_argument("--price-in", type=float, default=2.0, help="輸入單價 USD/百萬 token(預設 Sonnet 5.5)")
    ap.add_argument("--price-out", type=float, default=10.0, help="輸出單價 USD/百萬 token")
    ap.add_argument("--mock", action="store_true", help="自我測試,不連網路")
    ap.add_argument("--target", type=float, default=0.9, help="『誤差 10% 以內』的目標比例")
    args = ap.parse_args()
    if not (args.direct or args.endpoint or args.mock):
        ap.error("請指定 --direct、--endpoint 或 --mock 其中之一")

    args.hints_list = load_hints(args.hints)
    products, truth = load_products(args.products), load_truth(args.truth)
    if not truth:
        sys.exit("ground_truth.csv 沒有資料")
    rows, unknown_all, failed, usages = [], [], [], []
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
            items, usage = recognize_retry(args, path, products)
            if items is None:
                failed.append(photo)
                continue
            if usage:
                usages.append(usage)
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
    summarize(rows, unknown_all, args.target, usages, args)
    if failed:
        print(f"\n失敗或略過的照片:{', '.join(failed)}")
    print(f"\n明細已存:{args.out}.csv、{args.out}_unknown.json")


if __name__ == "__main__":
    main()
