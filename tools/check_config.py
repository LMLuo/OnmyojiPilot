"""
配置自检工具 —— 在联调之前，先把"静态就能查出来"的错误全查一遍

为什么需要
----------
模板匹配有一类错误是**静默失败**的：
    roi_back 比模板还小  -> 模板永远塞不进搜索区，这一项就永远不命中，
                             而日志里只会看到"没识别到按钮"，很难往配置上想。
还有：

    roi 越界            -> 裁剪后区域为空/异常，匹配结果毫无意义
    模板文件缺失/损坏   -> 加载时只有一句 warning，容易漏看
    两个模板同名不同文件-> 后加载的覆盖前面的，配置白写

这个脚本把这些都变成显式报错，改动配置后跑一下，比开设备试快得多。

用法：
    python tools/check_config.py            # 检查所有任务
    python tools/check_config.py hero_test  # 只检查一个

退出码：0=全过，1=有问题
"""
import os
import sys

import cv2

BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT_ROOT)

from core.window import BASE_W, BASE_H        # noqa: E402
from tasks.registry import TASKS              # noqa: E402


def check_task(spec, verbose=True):
    """返回 (错误列表, 警告列表)"""
    errors, warns = [], []

    if not spec.templates:
        return errors, warns          # 未实现的任务没有模板，跳过

    if verbose:
        print(f"\n=== {spec.id}  {spec.name} ===")
        print(f"    模板目录: {spec.template_dir}")

    if not os.path.isdir(spec.template_dir):
        errors.append(f"模板目录不存在: {spec.template_dir}")
        return errors, warns

    seen_files = {}
    for name, cfg in spec.templates.items():
        fname = cfg.get("file")
        if not fname:
            errors.append(f"[{name}] 缺少 file 字段")
            continue

        # ---- 文件存在且能读 ----
        path = os.path.join(spec.template_dir, fname)
        if not os.path.exists(path):
            errors.append(f"[{name}] 模板文件不存在: {fname}")
            continue
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            errors.append(f"[{name}] 模板读取失败（文件损坏？）: {fname}")
            continue
        th, tw = img.shape[:2]

        # ---- 退化模板（纯色会匹配一切）----
        if float(img.std()) < 1e-3:
            warns.append(f"[{name}] 模板几乎纯色（std≈0），匹配会不可靠")

        # ---- 同名文件复用 ----
        if fname in seen_files:
            warns.append(f"[{name}] 与 [{seen_files[fname]}] 共用同一张图: {fname}")
        seen_files[fname] = name

        # ---- roi 检查 ----
        roi_back = cfg.get("roi_back")
        roi_front = cfg.get("roi_front")
        threshold = cfg.get("threshold", 0.85)

        if not (0.0 < float(threshold) <= 1.0):
            errors.append(f"[{name}] threshold 越界: {threshold}")

        if roi_back is not None:
            x, y, w, h = roi_back
            if x < 0 or y < 0:
                errors.append(f"[{name}] roi_back 坐标为负: {roi_back}")
            if x + w > BASE_W or y + h > BASE_H:
                errors.append(f"[{name}] roi_back 超出基准画面 "
                              f"{BASE_W}x{BASE_H}: {roi_back}")
            # 核心检查：搜索区必须装得下模板（最小缩放档也要能放）
            if w < tw or h < th:
                errors.append(
                    f"[{name}] roi_back {w}x{h} 小于模板 {tw}x{th} —— "
                    f"这一项**永远不会命中**，必须把搜索区放大")
            # 只留一点点余量也不好：多尺度放不进去，窗口一变就漏检
            elif w < tw * 1.05 or h < th * 1.05:
                warns.append(
                    f"[{name}] roi_back {w}x{h} 仅比模板 {tw}x{th} 略大，"
                    f"多尺度档位会被跳过，窗口放大时容易漏检")

        if roi_front is not None:
            x, y, w, h = roi_front
            if x < 0 or y < 0:
                errors.append(f"[{name}] roi_front 坐标为负: {roi_front}")
            if x + w > BASE_W or y + h > BASE_H:
                errors.append(f"[{name}] roi_front 超出基准画面: {roi_front}")
            if w < 4 or h < 4:
                errors.append(f"[{name}] roi_front 太小（{w}x{h}），"
                              f"随机取点会落在同一像素，失去防机械点击意义")

        if verbose:
            rb = f"{roi_back}" if roi_back else "全图"
            print(f"    {name:18s} 模板 {tw:3d}x{th:<3d} 搜索区 {rb:18s} "
                  f"阈值 {threshold:.2f}")

    return errors, warns


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None

    total_err, total_warn = 0, 0
    for spec in TASKS:
        if only and spec.id != only:
            continue
        errors, warns = check_task(spec)
        for e in errors:
            print(f"  [错误] {e}")
        for w in warns:
            print(f"  [警告] {w}")
        total_err += len(errors)
        total_warn += len(warns)

    print("\n" + "-" * 60)
    if total_err:
        print(f"发现 {total_err} 个错误、{total_warn} 个警告 —— 先修错误再上设备")
        return 1
    print(f"配置检查通过（{total_warn} 个警告，不阻塞）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
