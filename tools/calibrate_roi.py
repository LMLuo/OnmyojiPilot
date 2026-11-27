"""
模板 roi 自校准工具（通用，支持任意已注册任务）

干什么用
--------
我们所有 roi 都是从 OAS 的 1280x720 经 core/coord.py 换算过来的。
换算是"算出来应该在哪"，但实际画面可能有几像素到几十像素的偏差。
这个工具不靠猜：它把当前画面**归一化到 878x404 基准**，
然后对每张模板做**全图多尺度匹配**（不给 roi，让它自己找），
量出真实命中位置，再和配置里的 roi 对比，报偏差。

怎么用
------
1. 在游戏里手动停在目标界面（例如「鬼兵演武」页面）
2. 运行：
       python tools/calibrate_roi.py --task hero_test
3. 看输出的偏差表：
   - 偏差都在 10px 内 -> 配置基本可用，直接跑任务
   - 偏差明显且**方向一致** -> 说明是整体平移，加 --write 会自动
     把修正量写进 history/coord_calibration.json（core/coord.py 会自动载入）
   - 偏差方向乱七八糟 -> 多半不是同一个界面，或模板版本不对

参数
----
    --task hero_test     指定任务（默认 hero_test）
    --write              把整体平移修正写入标定文件
    --save-dir DIR       顺带把归一化后的基准画面和匹配可视化存到 DIR
    --min-score 0.6      低于这个分数视为没匹配上，不参与偏差统计

注意
----
本工具**只截图、不点击**，可以安全地在任何界面运行。
它需要 ZenLink 正在投屏（PrintWindow 能抓到画面）。
"""
import os
import sys
import argparse

import cv2
import numpy as np

BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT_ROOT)

from core.window import WindowDevice, fit_transform, BASE_W, BASE_H   # noqa: E402
from core.matcher import Template                                     # noqa: E402
from core import coord                                                # noqa: E402
from tasks.registry import TASK_MAP                                   # noqa: E402


def to_base_frame(bgr, base_w=BASE_W, base_h=BASE_H):
    """
    把窗口画面归一化成 878x404 基准帧。

    和 matcher 的映射互为逆运算：先按 contain 求出内容区，
    裁出来再缩放到基准尺寸。这样之后的坐标就是纯基准坐标，
    不用再考虑黑边偏移。
    """
    h, w = bgr.shape[:2]
    scale, ox, oy = fit_transform(w, h, base_w, base_h)
    x1, y1 = int(round(ox)), int(round(oy))
    x2 = int(round(ox + base_w * scale))
    y2 = int(round(oy + base_h * scale))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = bgr[y1:y2, x1:x2]
    return cv2.resize(crop, (base_w, base_h), interpolation=cv2.INTER_AREA)


def match_full(base_bgr, tpl_bgr, scales):
    """
    全图多尺度匹配，返回 (best_score, (cx, cy), best_scale, best_rect)

    直接调 cv2.matchTemplate，不走 Template.match —— 因为那边会强制用
    roi_back 限范围，而这里恰恰要看"不限范围时它在哪"。
    """
    best = (-1.0, None, None, None)
    for s in scales:
        tw = max(6, int(round(tpl_bgr.shape[1] * s)))
        th = max(6, int(round(tpl_bgr.shape[0] * s)))
        if tw > base_bgr.shape[1] or th > base_bgr.shape[0]:
            continue
        tpl = cv2.resize(tpl_bgr, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(base_bgr, tpl, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(res)
        if max_v > best[0]:
            cx = max_l[0] + tw / 2.0
            cy = max_l[1] + th / 2.0
            best = (float(max_v), (cx, cy), s, (max_l[0], max_l[1], tw, th))
    return best


def center_of(roi):
    if roi is None:
        return None
    x, y, w, h = roi
    return (x + w / 2.0, y + h / 2.0)


def main():
    ap = argparse.ArgumentParser(description="模板 roi 自校准（通用）")
    ap.add_argument("--task", default="hero_test")
    ap.add_argument("--write", action="store_true",
                    help="把整体平移修正写入 history/coord_calibration.json")
    ap.add_argument("--save-dir", default=None, help="保存基准帧与匹配可视化")
    ap.add_argument("--min-score", type=float, default=0.6)
    args = ap.parse_args()

    spec = TASK_MAP.get(args.task)
    if spec is None:
        print(f"未知任务: {args.task}（可选: {', '.join(TASK_MAP)}）")
        return 1
    if not spec.available or not spec.templates:
        print(f"任务 {args.task} 还没实现，没有模板可校准")
        return 1

    # ---- 截图 ----
    dev = WindowDevice()
    if not dev.find_window():
        print("找不到 ZenLink 窗口。请确认：")
        print("  1) ZenLink 已打开并正在投屏")
        print("  2) 窗口没有被关闭或改标题")
        return 1
    print(f"窗口 {dev.width}x{dev.height}")

    rgb = dev.screenshot()
    if WindowDevice.is_idle_frame(rgb):
        print("画面看起来是 ZenLink 的待机界面（投屏已断开），请先点「开始投屏」")
        return 1
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    base = to_base_frame(bgr)
    if base is None:
        print("画面归一化失败，窗口尺寸异常")
        return 1
    print(f"已归一化到基准帧 {base.shape[1]}x{base.shape[0]}")

    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
        cv2.imwrite(os.path.join(args.save_dir, "base_frame.png"), base)

    # 搜索范围放宽到 0.80~1.25：如果真实缩放不是 0.5611，
    # 这里能看出来（命中档位会明显偏离 1.00）
    scales = [round(0.80 + 0.05 * i, 2) for i in range(10)]

    print("\n%-16s %6s %8s %-18s %-18s %s" %
          ("模板", "分数", "命中尺寸", "命中中心(BASE)", "配置中心(BASE)", "偏差"))
    print("-" * 92)

    dxs, dys = [], []
    rows = []
    for name, cfg in spec.templates.items():
        path = os.path.join(spec.template_dir, cfg["file"])
        tpl = cv2.imread(path, cv2.IMREAD_COLOR)
        if tpl is None:
            print(f"{name:16s}   模板文件读不到: {path}")
            continue

        score, center, step, rect = match_full(base, tpl, scales)
        exp = center_of(cfg.get("roi_front"))
        if step is None or score < args.min_score or exp is None:
            flag = "未命中" if score < args.min_score else "无 roi_front"
            print(f"{name:16s} {score:6.3f}        -  {str(center):18s} {str(exp):18s} {flag}")
            continue

        dx = center[0] - exp[0]
        dy = center[1] - exp[1]
        size = f"{int(rect[2])}x{int(rect[3])}@{step:.2f}"
        print(f"{name:16s} {score:6.3f} {size:>8s} "
              f"({center[0]:6.1f},{center[1]:6.1f})  "
              f"({exp[0]:6.1f},{exp[1]:6.1f})  "
              f"dx={dx:+6.1f} dy={dy:+6.1f}")
        dxs.append(dx)
        dys.append(dy)
        rows.append((name, center, rect, score))

    if not dxs:
        print("\n没有任何模板命中。可能原因：")
        print("  - 当前界面不是该任务的页面（先手动停到对应界面）")
        print("  - 游戏界面有改动，模板图过期了")
        return 1

    mdx = float(np.median(dxs))
    mdy = float(np.median(dys))
    spread_x = float(np.max(dxs) - np.min(dxs))
    spread_y = float(np.max(dys) - np.min(dys))

    print("-" * 92)
    print(f"命中 {len(dxs)}/{len(spec.templates)} 个模板")
    print(f"偏差中位数  dx={mdx:+.1f}  dy={mdy:+.1f}   （极差 {spread_x:.0f} / {spread_y:.0f}）")

    if abs(mdx) <= 10 and abs(mdy) <= 10 and spread_x <= 20 and spread_y <= 20:
        print("\n结论：偏差很小，配置可以直接用，不用标定。")
        return 0

    if spread_x > 25 or spread_y > 25:
        print("\n结论：偏差方向不一致（极差过大），不是整体平移。")
        print("      多半不是同一个界面，或某几张模板已经过期。")
        print("      建议先用 --save-dir 存图，看看 base_frame.png 是什么画面。")
        return 1

    print("\n结论：偏差方向一致，属于整体平移。")
    if not args.write:
        print(f"      可以加 --write 把修正量写入标定文件：")
        print(f"      offset_x {coord.DEFAULT.offset_x:.2f} -> {coord.DEFAULT.offset_x + mdx:.2f}")
        print(f"      offset_y {coord.DEFAULT.offset_y:.2f} -> {coord.DEFAULT.offset_y + mdy:.2f}")
        print("      （注意：加了 --write 之后 ROI 会整体平移，")
        print("        config 里的数字不用改，core/coord.py 会自动读标定文件）")
        return 0

    inst = coord.OasToBase(use_calibration=False)
    path = inst.save_calibration(
        scale=inst.scale,
        offset_x=inst.offset_x + mdx,
        offset_y=inst.offset_y + mdy,
    )
    print(f"      已写入 {path}")
    print(f"      S={inst.scale:.6f}  OX={inst.offset_x:.3f}  OY={inst.offset_y:.3f}")
    print("      重新跑一次本工具确认偏差归零；如需撤销，删掉这个 json 即可。")

    if args.save_dir and rows:
        vis = base.copy()
        for name, center, rect, score in rows:
            cv2.rectangle(vis, (rect[0], rect[1]),
                          (rect[0] + rect[2], rect[1] + rect[3]), (0, 200, 0), 1)
            cv2.putText(vis, f"{name}", (rect[0], max(10, rect[1] - 3)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 200, 0), 1)
        cv2.imwrite(os.path.join(args.save_dir, "matched.png"), vis)
        print(f"      匹配可视化: {os.path.join(args.save_dir, 'matched.png')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
