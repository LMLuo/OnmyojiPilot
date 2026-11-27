"""
联调小工具：在基准坐标上点一下，然后截图存盘，方便一步步摸界面。

校准阶段用，不是运行时依赖。用法：
    python tools/step.py click 260 178          # 点基准坐标 (260,178)
    python tools/step.py shot                  # 只截图存盘
    python tools/step.py shot out.png          # 指定文件名

点完会等一会儿再截图（默认 1.5s），存到 history/_shots/ 下。
"""
import os
import sys
import time

BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT_ROOT)

from core.window import WindowDevice   # noqa: E402

SHOT_DIR = os.path.join(BOT_ROOT, "history", "_shots")


def main():
    os.makedirs(SHOT_DIR, exist_ok=True)
    dev = WindowDevice()
    if not dev.find_window():
        print("没找到 ZenLink 投屏窗口")
        return 1
    print("窗口 %dx%d" % (dev.width, dev.height))

    action = sys.argv[1] if len(sys.argv) > 1 else "shot"
    if action == "click":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        wait = float(sys.argv[4]) if len(sys.argv) > 4 else 1.5
        print("点击基准坐标 (%d, %d)，等待 %.1fs" % (x, y, wait))
        dev.click(x, y)
        time.sleep(wait)

    name = sys.argv[2] if (action == "shot" and len(sys.argv) > 2) else \
        ("after_click.png" if action == "click" else "shot.png")
    img = dev.screenshot()
    path = os.path.join(SHOT_DIR, name)
    import cv2
    cv2.imwrite(path, img)
    print("截图: %s  (%dx%d)" % (path, img.shape[1], img.shape[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
