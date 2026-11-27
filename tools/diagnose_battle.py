"""
战斗时长诊断：点挑战后高频采样，记录
- 每次截图是否成功、耗时
- reward 领奖图标首次出现的时刻
- 画面均值变化（判断战斗是否结束）
用于定位"战斗20秒但40秒才检测到领奖"的延迟来源。

用法：python tools/diagnose_battle.py
不操作鼠标，全部后台消息点击。
"""
import os
import sys
import time
import ctypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from core.window import WindowDevice, user32, gdi32
from core.matcher import Matcher
from config import TEMPLATES

BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def raw_capture(dev):
    """直接调 PrintWindow，返回 (ok, bgr, 耗时)"""
    w, h = dev.width, dev.height
    t0 = time.time()
    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    gdi32.SelectObject(mem_dc, bitmap)
    ok = bool(user32.PrintWindow(dev.video_hwnd, mem_dc, 2))
    raw = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetBitmapBits(bitmap, w * h * 4, raw)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)
    cost = (time.time() - t0) * 1000
    if not ok:
        return False, None, cost
    img = np.frombuffer(raw.raw, dtype=np.uint8).reshape(h, w, 4)
    bgr = img[:, :, :3].copy()
    return True, bgr, cost


def main():
    dev = WindowDevice(click_mode="message")
    if not dev.find_window():
        print("窗口定位失败")
        return

    matcher = Matcher(os.path.join(BOT_ROOT, "tasks", "orochi", "templates"))
    matcher.load_config(TEMPLATES)

    # 先找挑战按钮
    ok, bgr, _ = raw_capture(dev)
    if not ok:
        print("首帧截图失败")
        return
    hit, score, pos = matcher.find("fire_btn", bgr)
    if not hit:
        print(f"未找到挑战按钮 (score={score:.3f})，请确认在御魂副本界面")
        return

    print(f"找到挑战按钮 @{pos}，点击开始战斗...")
    dev.click(pos[0], pos[1], duration=0.12)

    print("\n时间(s) | 截图ok | 耗时ms | 均值 | reward分 | 状态")
    print("-" * 70)
    t_start = time.time()
    first_reward = None
    last_mean = None
    fail_count = 0

    while time.time() - t_start < 70:
        elapsed = time.time() - t_start
        ok, bgr, cost = raw_capture(dev)
        if not ok:
            fail_count += 1
            print(f"{elapsed:7.2f} |   ✗    | {cost:6.0f} |  --  |   --   | 截图失败")
            time.sleep(0.15)
            continue

        mean = float(bgr.mean())
        r_hit, r_score, r_pos = matcher.find("reward", bgr)
        # 也检查挑战按钮是否还在（在=还没进战斗或已结束回界面）
        f_hit, f_score, _ = matcher.find("fire_btn", bgr)

        state = []
        if r_hit:
            state.append("领奖出现")
            if first_reward is None:
                first_reward = elapsed
        if f_hit:
            state.append("挑战按钮可见")
        if last_mean is not None and abs(mean - last_mean) > 25:
            state.append("画面剧变")

        print(f"{elapsed:7.2f} |   ✓    | {cost:6.0f} | {mean:4.0f} |  {r_score:.3f}  | {' '.join(state)}")
        last_mean = mean

        if r_hit:
            print(f"\n>>> 领奖图标首次出现于 {first_reward:.2f}s，截图失败 {fail_count} 次")
            # 保存此刻画面
            cv2.imwrite(os.path.join(BOT_ROOT, "diag_reward_frame.png"), bgr)
            break

        time.sleep(0.15)

    if first_reward is None:
        print(f"\n>>> 70秒内未检测到领奖图标，截图失败 {fail_count} 次")


if __name__ == "__main__":
    main()
