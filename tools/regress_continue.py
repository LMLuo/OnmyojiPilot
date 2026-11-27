"""
click_continue 双重校验回归

背景：
    该模板是"近黑底 + 细亮字"，TM_CCOEFF_NORMED 在暗背景上方差极小，
    任意亮斑都能把分数顶高。实测战斗帧能到 0.66~0.84，真掉落页 0.99，
    窗口缩小到 540x249 时两者区间重叠 —— 单靠阈值分不开。
    因此加了二值亮部结构校验（Template.verify_binary）。

本脚本对 正样本 / 负样本 × 全尺寸 做回归，要求：
    正样本全部命中；负样本全部不命中。

用法：
    python tools/regress_continue.py
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.window import WindowDevice, fit_transform
from core.matcher import Matcher
from config import TEMPLATES

BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_W, BASE_H = 878, 404

SIZES = [
    (540, 249), (560, 258), (600, 276), (680, 313),
    (739, 340), (800, 368), (878, 404), (999, 458),
    (1100, 506), (1280, 589), (1600, 736),
    (878, 458),   # 上下黑边
    (1280, 404),  # 左右黑边
]

# 真掉落页（右下角有"点击屏幕继续"）
# 注意：grid_loot.png 上画了坐标网格，网格线正好压在文字上，
# 会把细字结构破坏掉（小窗口下 CCOEFF 只有 0.59），不是有效正样本，故排除。
POS = ["verify_loot.png", "diag_reward_frame.png", "shot_stuck.png"]
# 真负样本：战斗中 / 御魂副本页
NEG = ["debug_now.png", "shot_soul.png", "grid_soul.png"]


def to_window(frame_bgr, win_w, win_h):
    """把原生帧按 contain 映射塞进指定窗口尺寸的黑底画布"""
    scale, ox, oy = fit_transform(win_w, win_h, BASE_W, BASE_H)
    cw, ch = round(BASE_W * scale), round(BASE_H * scale)
    ox, oy = int(round(ox)), int(round(oy))
    canvas = np.zeros((win_h, win_w, 3), np.uint8)
    resized = cv2.resize(frame_bgr, (cw, ch),
                         interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    canvas[oy:oy + ch, ox:ox + cw] = resized
    return canvas


def main():
    matcher = Matcher(os.path.join(BOT_ROOT, "tasks", "orochi", "templates"))
    matcher.load_config(TEMPLATES)
    t = matcher.templates["click_continue"]

    def score(img):
        """返回 (命中, CCOEFF 最高分, 二值结构分)"""
        ok, sc, _ = t.match(img)
        # 单独再算一次二值分，便于观察两个指标各自的分布
        fit = t._fits(img)[0]
        roi = t._scaled_roi(t.roi_back, img, fit)
        search, ox, oy = t._crop(img, roi)
        best, size, loc = -1.0, None, None
        for step in t.scales:
            w = max(8, round(t.image.shape[1] * fit[0] * step))
            h = max(8, round(t.image.shape[0] * fit[1] * step))
            if w > search.shape[1] or h > search.shape[0]:
                continue
            tpl = cv2.resize(t.image, (w, h), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
            res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
            _, mx, _, ml = cv2.minMaxLoc(res)
            if mx > best:
                best, size, loc = mx, (w, h), ml
        if size is None:
            return ok, 0.0, 0.0
        patch = search[loc[1]:loc[1] + size[1], loc[0]:loc[0] + size[0]]
        tpl = cv2.resize(t.image, size, interpolation=cv2.INTER_AREA)
        return ok, best, t._binary_agreement(tpl, patch)

    print("=" * 78)
    print(f"阈值 CCOEFF >= {t.threshold}  且  二值结构 >= {t.verify_binary}")
    print("=" * 78)

    fails = 0
    for tag, names, want_hit in (("正样本(掉落页)", POS, True), ("负样本(战斗/御魂)", NEG, False)):
        print(f"\n【{tag}】期望 {'命中' if want_hit else '不命中'}")
        print(f"{'样本':<24}{'窗口':>12}{'CCOEFF':>9}{'二值':>8}  结果")
        print("-" * 78)
        for name in names:
            path = os.path.join(BOT_ROOT, name)
            frame = cv2.imread(path, cv2.IMREAD_COLOR)
            if frame is None:
                print(f"{name:<24} 读不到，跳过")
                continue
            for w, h in SIZES:
                canvas = to_window(frame, w, h)
                ok, cc, bs = score(canvas)
                good = (ok == want_hit)
                if not good:
                    fails += 1
                mark = "OK" if good else "!! 不符合预期"
                print(f"{name:<24}{w:>5}x{h:<6}{cc:>9.3f}{bs:>8.3f}  {mark}")
        print()

    print("=" * 78)
    print(f"合计不符合预期: {fails} 项")
    print("=" * 78)
    return fails


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
