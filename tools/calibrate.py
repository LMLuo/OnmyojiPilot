"""
模板标定工具：在游戏切到指定界面后运行，截取画面并交互式框选按钮保存为模板。

用法：
    python tools/calibrate.py

流程：
1. 先把 ZenLink 投屏切到目标界面（如庭院、御魂界面、战斗结算）
2. 运行脚本 → 输入模板名称 → 鼠标框选区域 → 自动保存为模板图片
"""
import os
import sys
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.window import WindowDevice

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tasks", "orochi", "templates")

drawing = False
start_pt = None
end_pt = None
current_img = None


def mouse_callback(event, x, y, flags, param):
    global drawing, start_pt, end_pt, current_img
    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        start_pt = (x, y)
        end_pt = (x, y)
    elif event == cv2.EVENT_MOUSEMOVE and drawing:
        end_pt = (x, y)
        img = current_img.copy()
        cv2.rectangle(img, start_pt, end_pt, (0, 255, 0), 2)
        cv2.imshow("calibrate", img)
    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        end_pt = (x, y)
        img = current_img.copy()
        cv2.rectangle(img, start_pt, end_pt, (0, 255, 0), 2)
        cv2.imshow("calibrate", img)


def main():
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    device = WindowDevice()
    if not device.find_window():
        print("窗口定位失败，请确认 ZenLink 已打开并显示游戏画面")
        return

    global current_img
    rgb = device.screenshot()
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    current_img = bgr.copy()

    print("\n" + "=" * 60)
    print("模板标定工具")
    print("=" * 60)
    print(f"画面尺寸: {bgr.shape[1]}x{bgr.shape[0]}")
    print("操作说明:")
    print("  1. 在弹出的窗口里按住鼠标左键框选要识别的按钮/文字")
    print("  2. 松开后回到终端输入模板名称（英文，如 fight_btn）")
    print("  3. 输入 q 退出并保存")
    print("=" * 60 + "\n")

    cv2.namedWindow("calibrate")
    cv2.setMouseCallback("calibrate", mouse_callback)
    cv2.imshow("calibrate", current_img)

    while True:
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        if key == ord('r'):
            rgb = device.screenshot()
            current_img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            cv2.imshow("calibrate", current_img)
            print("已刷新截图")
        if key == ord('s') and start_pt and end_pt:
            name = input("模板名称（英文，如 fight_btn）: ").strip()
            if not name:
                continue
            x1, y1 = start_pt
            x2, y2 = end_pt
            if x1 > x2:
                x1, x2 = x2, x1
            if y1 > y2:
                y1, y2 = y2, y1
            if x2 - x1 < 5 or y2 - y1 < 5:
                print("框选区域太小，请重新框选")
                continue
            crop = current_img[y1:y2, x1:x2]
            path = os.path.join(TEMPLATE_DIR, f"{name}.png")
            cv2.imwrite(path, crop)
            print(f"  已保存: {path}  (区域 x={x1}-{x2}, y={y1}-{y2}, 尺寸 {x2-x1}x{y2-y1})")
            print(f"  中心点坐标: ({x1 + (x2-x1)//2}, {y1 + (y2-y1)//2})")
            start_pt = end_pt = None

    cv2.destroyAllWindows()
    print("\n标定完成。模板保存在:", TEMPLATE_DIR)


if __name__ == "__main__":
    main()
