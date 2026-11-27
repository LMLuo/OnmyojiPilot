"""
从 OAS 项目导入现成的识别模板，并按投屏分辨率缩放。

OAS 资产基于 1280x720，ZenLink 投屏约 739x340。
本脚本：
1. 解析 OAS 的 assets.py 提取 RuleImage 定义（file / threshold / roi）
2. 复制并缩放 PNG 到 OnmyojiPilot 的模板目录
3. 生成 templates_config.py（供 matcher 加载）

用法：python tools/import_oas_assets.py
"""
import os
import re
import shutil
import cv2

# 路径
OAS_ROOT = r"D:\ACompanyProject-Python\yys"
BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BOT_ROOT, "tasks", "orochi", "templates")

SOURCE_W, SOURCE_H = 1280, 720

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

    # 从当前画面读取实际缩放比
    sys_path = os.path.join(BOT_ROOT, "core")
    import sys
    sys.path.insert(0, BOT_ROOT)
    from core.window import WindowDevice
    dev = WindowDevice()
    if not dev.find_window():
        print("警告: 无法定位窗口，使用默认缩放比")
        sx, sy = 739 / SOURCE_W, 340 / SOURCE_H
    else:
        sx = dev.width / SOURCE_W
        sy = dev.height / SOURCE_H
        print(f"投屏尺寸: {dev.width}x{dev.height}, 缩放比: {sx:.4f}, {sy:.4f}")

    config_lines = ["# 自动生成的模板配置（由 import_oas_assets.py 生成，勿手改）", "TEMPLATES = {"]
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
        # 关键：必须【等比】缩放！OAS 原图是 1280x720 下截取的，
        # 非等比缩放（x/y 用不同比例）会让模板变形，匹配分数从 0.97 掉到 0.6。
        # 用高度比例作为统一缩放因子（高度受画面比例影响更小）。
        scale = sy
        new_w = max(1, round(img.shape[1] * scale))
        new_h = max(1, round(img.shape[0] * scale))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        out_name = f"{new_name}.png"
        cv2.imwrite(os.path.join(OUT_DIR, out_name), resized)

        # roi_front 也按统一比例缩放
        fx, fy, fw, fh = info["roi_front"]
        new_roi = (round(fx * scale), round(fy * scale),
                   max(1, round(fw * scale)), max(1, round(fh * scale)))

        config_lines.append(
            f'    "{new_name}": {{"file": "{out_name}", "threshold": {info["threshold"]}, "roi_front": {new_roi}}},'
        )
        imported.append(new_name)
        print(f"  导入 {var_name} -> {new_name}  {img.shape[1]}x{img.shape[0]} -> {new_w}x{new_h}")

    config_lines.append("}")
    config_path = os.path.join(BOT_ROOT, "tasks", "orochi", "templates_config.py")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write("\n".join(config_lines) + "\n")

    print(f"\n完成: 导入 {len(imported)} 个模板")
    print(f"模板目录: {OUT_DIR}")
    print(f"配置文件: {config_path}")
    print("导入的模板:", ", ".join(imported))


if __name__ == "__main__":
    main()
