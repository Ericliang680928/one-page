#!/usr/bin/env python3
"""評分「標點模式」:把模型標的點與人工標註的框一對一配對。

用法:
  python3 score_points.py --gt gt.json --pred out_points [--plain out_plain] [--imgs imgs --overlay overlay]

gt.json 格式:{"照片檔名": {"w": 原圖寬, "h": 原圖高, "boxes": [[x1,y1,x2,y2,類別], ...]}}
pred 資料夾:每張照片一個 <檔名>.json,內容為辨識回傳的 {"items":[{"count":..,"points":[[x,y],..]},..]}
  (points 為 0–1000 的比例座標)

指標:
  計數誤差   = 點數 − 標註件數
  命中率     = 落在某個「尚未被配對的框」內的點 / 全部的點(點有沒有標在商品上)
  涵蓋率     = 被點到的框 / 全部的框(有沒有漏標)
"""
import argparse
import json
import os
import re
import sys


def load_items(path):
    text = open(path, encoding="utf-8").read()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0)).get("items", [])
    except json.JSONDecodeError:
        return None


def points_of(items):
    pts = []
    for it in items:
        for p in it.get("points") or []:
            if isinstance(p, list) and len(p) >= 2:
                try:
                    pts.append((min(1000.0, max(0.0, float(p[0]))), min(1000.0, max(0.0, float(p[1])))))
                except (TypeError, ValueError):
                    pass
    return pts


def count_of(items):
    n = 0
    for it in items:
        p = it.get("points")
        if isinstance(p, list) and p:
            n += len(p)
        else:
            try:
                n += max(0, int(float(it.get("count", 0))))
            except (TypeError, ValueError):
                pass
    return n


def match(points, boxes):
    """二分圖最大配對(Kuhn):每個點最多配一個包含它的框,每個框最多配一個點。"""
    cand = [[j for j, (x1, y1, x2, y2) in enumerate(boxes) if x1 <= px <= x2 and y1 <= py <= y2]
            for px, py in points]
    owner = [-1] * len(boxes)

    def try_assign(i, seen):
        for j in cand[i]:
            if j in seen:
                continue
            seen.add(j)
            if owner[j] < 0 or try_assign(owner[j], seen):
                owner[j] = i
                return True
        return False

    hit = [try_assign(i, set()) for i in range(len(points))]
    # 重新由 owner 推回哪些點最終有配對
    matched_pts = {o for o in owner if o >= 0}
    return [i in matched_pts for i in range(len(points))], [o >= 0 for o in owner]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--pred", required=True, help="標點模式的輸出資料夾")
    ap.add_argument("--plain", help="純數字模式的輸出資料夾(對照)")
    ap.add_argument("--imgs", help="照片資料夾(要畫疊圖時需要)")
    ap.add_argument("--overlay", help="輸出疊圖的資料夾(需要 Pillow)")
    args = ap.parse_args()

    gt = json.load(open(args.gt, encoding="utf-8"))
    rows, tot = [], dict(gt=0, pts=0, hit=0, cov=0, abs=0, pabs=0, pn=0)
    for name in sorted(gt):
        g = gt[name]
        boxes = [(b[0] / g["w"] * 1000, b[1] / g["h"] * 1000, b[2] / g["w"] * 1000, b[3] / g["h"] * 1000) for b in g["boxes"]]
        f = os.path.join(args.pred, name + ".json")
        items = load_items(f) if os.path.exists(f) else None
        if items is None:
            rows.append((name, len(boxes), None, None, None, None, None))
            continue
        pts = points_of(items)
        hit, covered = match(pts, boxes)
        plain = None
        if args.plain:
            pf = os.path.join(args.plain, name + ".json")
            pi = load_items(pf) if os.path.exists(pf) else None
            plain = count_of(pi) if pi is not None else None
        rows.append((name, len(boxes), len(pts), sum(hit), sum(covered), plain, count_of(items)))
        tot["gt"] += len(boxes); tot["pts"] += len(pts); tot["hit"] += sum(hit); tot["cov"] += sum(covered)
        tot["abs"] += abs(len(pts) - len(boxes))
        if plain is not None:
            tot["pabs"] += abs(plain - len(boxes)); tot["pn"] += 1

        if args.overlay and args.imgs:
            from PIL import Image, ImageDraw
            os.makedirs(args.overlay, exist_ok=True)
            im = Image.open(os.path.join(args.imgs, name)).convert("RGB")
            d = ImageDraw.Draw(im)
            W, H = im.size
            for (x1, y1, x2, y2), c in zip(boxes, covered):
                d.rectangle([x1 / 1000 * W, y1 / 1000 * H, x2 / 1000 * W, y2 / 1000 * H],
                            outline=(0, 200, 0) if c else (255, 160, 0), width=4)
            r = max(8, W * 0.011)
            for (px, py), h in zip(pts, hit):
                cx, cy = px / 1000 * W, py / 1000 * H
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(37, 99, 235) if h else (225, 29, 72), outline=(255, 255, 255), width=3)
            im.save(os.path.join(args.overlay, name), quality=85)

    print(f"{'照片':24s} {'標註':>4s} {'點數':>4s} {'誤差':>4s} {'命中':>6s} {'涵蓋':>6s} {'純數字':>6s}")
    for name, n, p, h, c, plain, cnt in rows:
        if p is None:
            print(f"{name:24s} {n:4d}  (沒有結果)")
            continue
        print(f"{name:24s} {n:4d} {p:4d} {p - n:+4d} {h:3d}/{p:<3d} {c:3d}/{n:<3d} "
              f"{'' if plain is None else f'{plain}({plain - n:+d})'}")
    done = [r for r in rows if r[2] is not None]
    if not done:
        sys.exit("沒有可評分的結果")
    exact = sum(r[2] == r[1] for r in done)
    within1 = sum(abs(r[2] - r[1]) <= 1 for r in done)
    print(f"\n照片數 {len(done)};標註總件數 {tot['gt']},模型總點數 {tot['pts']}")
    print(f"計數:完全正確 {exact}/{len(done)} 張,誤差 ±1 以內 {within1}/{len(done)} 張,"
          f"平均絕對誤差 {tot['abs'] / len(done):.2f} 件/張,總量誤差 {(tot['pts'] - tot['gt']) / tot['gt'] * 100:+.1f}%")
    print(f"位置:命中率 {tot['hit'] / max(1, tot['pts']) * 100:.1f}%(點落在商品上),"
          f"涵蓋率 {tot['cov'] / tot['gt'] * 100:.1f}%(商品有被點到)")
    if tot["pn"]:
        pd = [r for r in done if r[5] is not None]
        print(f"對照(純數字模式):完全正確 {sum(r[5] == r[1] for r in pd)}/{len(pd)} 張,"
              f"平均絕對誤差 {tot['pabs'] / tot['pn']:.2f} 件/張")
    bad = [r for r in done if r[6] != r[2]]
    if bad:
        print(f"注意:{len(bad)} 張的 count 欄位與點數不一致(程式以點數為準)")


if __name__ == "__main__":
    main()
