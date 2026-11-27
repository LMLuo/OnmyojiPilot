import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cv2
from config import TEMPLATES
from core.window import WindowDevice
from core.matcher import Matcher

TEMPLATE_DIR = os.path.join("tasks", "orochi", "templates")
dev = WindowDevice()
if not dev.find_window():
    print("FIND WINDOW FAILED"); sys.exit(1)
m = Matcher(TEMPLATE_DIR); m.load_config(TEMPLATES)

CHECK = ["fire_btn", "reward", "click_continue", "courtyard"]

def snap(tag):
    img = dev.screenshot()
    out = []
    for n in CHECK:
        ok, sc, pos = m.find(n, img)
        out.append("%s=%.2f%s" % (n, sc, "*" if ok else ""))
    print("[%s] mean=%.1f  %s" % (tag, img.mean(), "  ".join(out)))
    return img

for step in range(6):
    img = snap("step%d" % step)
    cv2.imwrite("flow_%d.png" % step, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    ok, sc, pos = m.find("click_continue", img)
    if ok:
        print("   -> click continue at (400,180)")
        dev.click(400, 180)
    else:
        ok2, sc2, pos2 = m.find("reward", img)
        if ok2:
            print("   -> click reward at %s" % (pos2,))
            dev.click(pos2[0], pos2[1])
        else:
            print("   -> no known button, click center")
            dev.click(400, 200)
    time.sleep(1.2)
snap("final")
