"""
页面状态探针：不点击、只看，告诉你脚本"现在以为自己在哪"。

为什么要这个工具
----------------
英杰试炼的两个子玩法（鬼兵演武/传承试炼）共用同一个「挑战」按钮点位，
区别只在左下角的御灵头像。联调时最容易搞混的就是
"脚本到底认为我站在哪一层"，光看识别分数看不出来。
这个工具把脚本的**判断结论**直接打出来，比猜快得多。

用法
----
    python tools/probe_page.py                      # 默认 hero_test
    python tools/probe_page.py --task hero_test_ccsl
    python tools/probe_page.py --task hero_test_ccsl --save out.png

只截图、不点击，任何时候都能安全跑。
"""
import os
import sys
import argparse

import cv2

BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT_ROOT)

from core.window import WindowDevice      # noqa: E402
from core.matcher import Matcher          # noqa: E402
from tasks.registry import get_task       # noqa: E402


def build_task(task_id):
    spec = get_task(task_id)
    if spec.cls is None:
        raise SystemExit("任务 %s 还没实现" % task_id)

    device = WindowDevice()
    if not device.find_window():
        raise SystemExit("没找到 ZenLink 投屏窗口")

    matcher = Matcher(spec.template_dir)
    matcher.load_config(spec.templates)
    config = dict(spec.config)
    config.setdefault("click_regions", spec.click_regions)
    config.setdefault("settle_exclude", spec.settle_exclude)
    config.setdefault("timing", spec.timing)
    return spec, device, matcher, config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="hero_test")
    ap.add_argument("--save", default="")
    args = ap.parse_args()

    spec, device, matcher, config = build_task(args.task)
    task = spec.cls(device, matcher, config)

    print("任务: %s (%s)" % (spec.name, spec.id))
    if hasattr(task, "layer_name"):
        print("层  : %s / %s" % (task.layer_name, task.hero_name))
    print("窗口: %dx%d" % (device.width, device.height))
    print()

    task.screenshot_fresh()

    checks = []
    if hasattr(task, "has_challenge_button"):
        checks += [
            ("has_challenge_button 挑战按钮在", task.has_challenge_button),
            ("is_layer_page        在目标子页面", task.is_layer_page),
            ("is_other_layer_page  在别的子页面", task.is_other_layer_page),
            ("is_main_page         在英杰试炼主页", task.is_main_page),
            ("is_in_battle         处于战斗/结算", task.is_in_battle),
        ]

    for label, fn in checks:
        print("  %-34s %s" % (label, "是" if fn() else "否"))

    if hasattr(task, "check_level_max"):
        ok = task.check_level_max()
        print("  %-34s %s" % ("check_level_max 未满级可跑", "是" if ok else "否"))

    # 顺带把每张模板的实测分数打出来，方便判断阈值够不够。
    # matcher.match 返回的中心是**窗口像素**口径，这里换算回 878x404 基准，
    # 才能直接和 task_config 里的 roi_front 比对（否则窗口一变就对不上）。
    from core.window import fit_transform, BASE_W, BASE_H
    bh, bw = task.bgr.shape[:2]
    sx, ox, oy = fit_transform(bw, bh, BASE_W, BASE_H)

    def to_base(pt):
        return ((pt[0] - ox) / sx, (pt[1] - oy) / sx)

    print()
    print("  各模板当前画面得分：")
    rows = []
    for name in spec.templates:
        ok, score, pos = matcher.find(name, task.bgr)
        rows.append((score, name, ok, pos))
    for score, name, ok, pos in sorted(rows, reverse=True):
        mark = "命中" if ok else "  - "
        where = ""
        if ok and pos:
            bx, by = to_base(pos)
            where = "命中中心 (%5.1f,%5.1f) BASE" % (bx, by)
        print("    %-18s %.3f  %s  %s" % (name, score, mark, where))
    print("  （窗口 %dx%d，比例 %.3f，黑边偏移 %.1f,%.1f）"
          % (device.width, device.height, sx, ox, oy))

    out = args.save or os.path.join(BOT_ROOT, "history", "_shots", "probe_%s.png" % args.task)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv2.imwrite(out, task.image)
    print("\n画面已存: %s" % out)


if __name__ == "__main__":
    main()
