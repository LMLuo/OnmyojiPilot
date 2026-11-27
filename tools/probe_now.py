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
img = dev.screenshot()
print("capture:", img.shape, "mean=%.1f" % img.mean())
cv2.imwrite("diag_probe.png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))

m = Matcher(TEMPLATE_DIR)
m.load_config(TEMPLATES)

for name in TEMPLATES:
    t0 = time.time()
    ok, score, pos = m.find(name, img)
    print("%-14s ok=%-5s score=%.3f pos=%s  (%.0fms)" % (name, ok, score, pos, (time.time()-t0)*1000))
