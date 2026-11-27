"""
诊断工具：实时检测当前画面能识别到哪些 OAS 模板，并显示匹配位置和分数。

用法：
    python tools/diagnose.py

输出：
- 当前画面截图（带识别框）保存到 debug_view.png
- 终端打印每个模板的最高分
"""
import os
import sys
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.window import WindowDevice
from core.matcher import Matcher
from config import TEMPLATES

BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    device = WindowDevice()
    if not device.find_window():
        print("窗口定位失败")
        return

    rgb = device.screenshot()
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    display = bgr.copy()

    matcher = Matcher(os.path.join(BOT_ROOT, "tasks", "orochi", "templates"))
    matcher.load_config(TEMPLATES)

    print("\n" + "=" * 60)
    print(f"当前画面: {bgr.shape[1]}x{bgr.shape[0]}")
    print("=" * 60)

    hits = []
    for name, cfg in TEMPLATES.items():
        ok, score, pos = matcher.find(name, bgr)
        status = "命中" if ok else "   "
        print(f"  {status} {name:16s} score={score:.3f} pos={pos} 阈值={cfg['threshold']}")
        if ok and pos:
            hits.append((name, score, pos))

    for name, score, pos in hits:
        tpl = matcher.templates[name]
        h, w = tpl.image.shape[:2]
        x = pos[0] - w // 2
        y = pos[1] - h // 2
        cv2.rectangle(display, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(display, f"{name} {score:.2f}", (x, y - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

    out = os.path.join(BOT_ROOT, "debug_view.png")
    cv2.imwrite(out, display)
    print("\n带识别框的截图已保存:", out)
    print("=" * 60)


if __name__ == "__main__":
    main()
