"""
从 OAS 项目导入现成的识别模板，并缩放到本项目基准尺寸。

OAS 资产基于 1280x720；本项目统一用 BASE_W×BASE_H（878x404）作为识别基准，
matcher 在匹配时会按窗口实际尺寸再等比映射一次。
所以模板【必须】缩放到 BASE 口径，不能按"当前窗口尺寸"缩放——否则会被缩两次。

本脚本：
1. 解析 OAS 的 assets.py 提取 RuleImage 定义（file / threshold / roi）
2. 复制并按 BASE_H 等比缩放 PNG 到模板目录
3. 打印 roi 建议值（BASE 口径），供人工写入 config.py

注意：不自动生成配置文件。模板的 roi / 阈值需要人工按实际画面校准
（OAS 的 roi 是 1280x720 口径，直接换算过去往往有几十像素偏差）。

用法：python tools/import_oas_assets.py
"""
import os
import re
import cv2

# 路径
OAS_ROOT = r"D:\ACompanyProject-Python\yys"
BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BOT_ROOT, "tasks", "orochi", "templates")

SOURCE_W, SOURCE_H = 1280, 720
BASE_W, BASE_H = 878, 404
SCALE = BASE_H / SOURCE_H  # ≈0.5611，模板统一缩放比例

# 需要导入的资产：名称 -> (assets.py 相对路径, 变量名, OnmyojiPilot 中的新名字)
NEEDED = [
    # 庭院/导航
    ("tasks/GameUi/assets.py", "I_CHECK_MAIN", "courtyard"),
    ("tasks/GameUi/assets.py", "I_EXPLORATION_GOTO_SOUL_ZONE", "soul_entry"),
    ("tasks/GameUi/assets.py", "I_CHECK_SOUL_ZONES", "soul_page"),
    # 御魂副本
    ("tasks/Orochi/assets.py", "I_OROCHI_CHECK_11", "moans_layer"),
    ("tasks/Orochi/assets.py", "I_OROCHI_FIRE", "fire_btn"),
    # 战斗结算
    ("tasks/Component/GeneralBattle/assets.py", "I_REWARD", "reward"),
    ("tasks/Component/GeneralBattle/assets.py", "I_WIN", "win"),
    ("tasks/Component/GeneralBattle/assets.py", "I_FALSE", "battle_false"),
    ("tasks/Component/GeneralBattle/assets.py", "I_GREED_GHOST", "greed_ghost"),
]


def parse_assets(path):
    """解析 assets.py，返回 {变量名: {file, threshold, roi_front, roi_back}}"""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = r'(\w+)\s*=\s*RuleImage\(roi_front=\(([^)]+)\),\s*roi_back=\(([^)]+)\),\s*threshold=([\d.]+),.*?file="([^"]+)"\)'
    for m in re.finditer(pattern, content, re.DOTALL):
        name = m.group(1)
        def parse_tuple(s):
            nums = [int(x.strip()) for x in s.split(",")]
            if len(nums) == 4 and nums[2] > SOURCE_W:  # (x1,y1,x2,y2) 格式
                return (nums[0], nums[1], nums[2] - nums[0], nums[3] - nums[1])
            return tuple(nums)
        result[name] = {
            "roi_front": parse_tuple(m.group(2)),
            "roi_back": parse_tuple(m.group(3)),
            "threshold": float(m.group(4)),
            "file": m.group(5).lstrip("./"),
        }
    return result


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"基准尺寸 {BASE_W}x{BASE_H}，统一缩放比 {SCALE:.4f}")
    if not os.path.isdir(OAS_ROOT):
        print(f"错误: 找不到 OAS 项目 {OAS_ROOT}")
        print("请先把 https://github.com/nearaura/OnmyojiAutoScript 克隆到该路径")
        raise SystemExit(1)

    imported = []

    cache = {}
    for assets_rel, var_name, new_name in NEEDED:
        if assets_rel not in cache:
            cache[assets_rel] = parse_assets(os.path.join(OAS_ROOT, assets_rel))
        assets = cache[assets_rel]
        if var_name not in assets:
            print(f"  跳过 {var_name}（未在 {assets_rel} 中找到）")
            continue
        info = assets[var_name]
        src = os.path.join(OAS_ROOT, info["file"])
        if not os.path.exists(src):
            print(f"  跳过 {var_name}（图片不存在: {src}）")
            continue

        # 读取原图并缩放
        img = cv2.imread(src)
        if img is None:
            continue
        # 关键：必须【等比】缩放！非等比（x/y 用不同比例）会让模板变形，
        # 匹配分数从 0.97 掉到 0.6。以高度比例作为统一因子。
        new_w = max(1, round(img.shape[1] * SCALE))
        new_h = max(1, round(img.shape[0] * SCALE))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        out_name = f"{new_name}.png"
        cv2.imwrite(os.path.join(OUT_DIR, out_name), resized)

        # roi_front 换算到 BASE 口径，仅作参考打印
        fx, fy, fw, fh = info["roi_front"]
        base_roi = (round(fx * SCALE), round(fy * SCALE),
                    max(1, round(fw * SCALE)), max(1, round(fh * SCALE)))
        imported.append((new_name, out_name, base_roi, info["threshold"]))
        print(f"  导入 {var_name} -> {new_name}  {img.shape[1]}x{img.shape[0]} -> {new_w}x{new_h}")

    print(f"\n完成: 导入 {len(imported)} 个模板到 {OUT_DIR}")
    print("\nOAS 原始 roi 换算到 BASE 口径（参考值，务必按实际画面校准后再写入 config.py）：")
    for name, out_name, roi, th in imported:
        print(f'    "{name}": {{"file": "{out_name}", "threshold": {th}, "roi_front": {roi}}},')
    print("\n提示：roi_front 只决定点击落点，不决定识别范围；"
          "识别范围用 roi_back，建议只框住该按钮稳定出现的区域。")


if __name__ == "__main__":
    main()
