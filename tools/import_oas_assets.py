"""
从 OAS 项目导入现成的识别模板，并缩放到本项目基准尺寸。

OAS 资产基于 1280x720；本项目统一用 BASE_W×BASE_H（878x404）作为识别基准，
matcher 在匹配时会按窗口实际尺寸再等比映射一次。
所以模板【必须】缩放到 BASE 口径，不能按"当前窗口尺寸"缩放——否则会被缩两次。

本脚本：
1. 解析 OAS 的 assets.py 提取 RuleImage 定义（file / threshold / roi）
2. 复制并按 BASE_H 等比缩放 PNG 到对应模块的模板目录
3. 打印换算到 BASE 口径的 roi（走 core/coord.py，不手算），供人工核对后写入配置

注意：不自动生成配置文件。roi 是"换算值"而非"实机标定值"，
换算依据见 core/coord.py 顶部注释；个别项在 OAS 里本来就是手工调的，
需要实机校验一遍。

用法：
    python tools/import_oas_assets.py            # 列出可选任务
    python tools/import_oas_assets.py orochi     # 导入御魂模板
    python tools/import_oas_assets.py hero_test  # 导入英杰试炼模板
    python tools/import_oas_assets.py all
"""
import os
import re
import sys
import cv2

# 路径
OAS_ROOT = r"D:\ACompanyProject-Python\yys"
BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT_ROOT)

SOURCE_W, SOURCE_H = 1280, 720
BASE_W, BASE_H = 878, 404
SCALE = BASE_H / SOURCE_H  # ≈0.5611，模板统一缩放比例

GB = "tasks/Component/GeneralBattle/assets.py"

# 每个任务要导入的资产：(assets.py 相对路径, 变量名, 本项目中的名字)
TASK_ASSETS = {
    "orochi": {
        "out_dir": ("tasks", "orochi", "templates"),
        "items": [
            # 庭院/导航
            ("tasks/GameUi/assets.py", "I_CHECK_MAIN", "courtyard"),
            ("tasks/GameUi/assets.py", "I_EXPLORATION_GOTO_SOUL_ZONE", "soul_entry"),
            ("tasks/GameUi/assets.py", "I_CHECK_SOUL_ZONES", "soul_page"),
            # 御魂副本
            ("tasks/Orochi/assets.py", "I_OROCHI_CHECK_11", "moans_layer"),
            ("tasks/Orochi/assets.py", "I_OROCHI_FIRE", "fire_btn"),
            # 战斗结算
            (GB, "I_REWARD", "reward"),
            (GB, "I_WIN", "win"),
            (GB, "I_FALSE", "battle_false"),
            (GB, "I_GREED_GHOST", "greed_ghost"),
        ],
    },
    "hero_test": {
        "out_dir": ("tasks", "hero_test", "templates"),
        "items": [
            # 英杰试炼主页面（探索页点进来的那个总览页）
            ("tasks/GameUi/assets.py", "I_CHECK_HERO_TEST", "hero_test_page"),
            # 左侧"鬼兵演武"竖排标签
            ("tasks/HeroTest/assets.py", "I_GBB", "gbb_entry"),
            # 说明：I_CHECK_HERO1_EXP（鬼兵演武页的御灵头像）没导 —— 实测它在
            # 英杰试炼主页面上会在人物胸口误匹配到 0.693，作为页面标志不可靠。
            # 鬼兵演武页用 hero_fire 判就够了。
            # 右下角"挑战"按钮
            ("tasks/HeroTest/assets.py", "I_BATTLE", "hero_fire"),
            # 点挑战后弹出的「当前未开启经验加成，是否继续挑战？」确认框里的【开始挑战】
            # 这个弹窗挡在挑战按钮前面，不处理就会一直点被遮住的按钮（实测踩过）
            ("tasks/HeroTest/assets.py", "I_START_CHALLENGE", "confirm_start"),
            # 说明：OAS 用 I_REAL_MONEY（13x17 的"真钱"小图标）判断门票不足，
            # 但这么小的模板放在大范围搜索里误匹配率极高，收益不值。
            # 我们改成"点满次数仍未进入战斗"时直接提示可能是门票/体力不足。
            # 鬼兵演武左上角返回
            ("tasks/HeroTest/assets.py", "I_GBB_BACK", "gbb_back"),
            # 通用战斗结算（与御魂同一套，直接复用 OAS 源图，不跨模块拷贝）
            (GB, "I_REWARD", "reward"),
            (GB, "I_WIN", "win"),
            (GB, "I_FALSE", "battle_false"),
            (GB, "I_DE_WIN", "de_win"),
        ],
    },
}


def parse_assets(path):
    """解析 assets.py，返回 {变量名: {file, threshold, roi_front, roi_back}}"""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = (r'(\w+)\s*=\s*RuleImage\(roi_front=\(([^)]+)\),\s*'
               r'roi_back=\(([^)]+)\),\s*threshold=([\d.]+),.*?file="([^"]+)"\)')
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


def import_task(task_name, verbose=True):
    """导入一个任务的全部模板，返回导入清单"""
    spec = TASK_ASSETS.get(task_name)
    if spec is None:
        raise KeyError(f"未知任务: {task_name}（可选: {', '.join(TASK_ASSETS)}）")

    from core.coord import oas_roi
    out_dir = os.path.join(BOT_ROOT, *spec["out_dir"])
    os.makedirs(out_dir, exist_ok=True)

    if verbose:
        print(f"\n=== 导入 {task_name} ===")
        print(f"基准 {BASE_W}x{BASE_H}，缩放比 {SCALE:.4f}，输出 {out_dir}")

    imported = []
    cache = {}
    for assets_rel, var_name, new_name in spec["items"]:
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

        img = cv2.imread(src)
        if img is None:
            print(f"  跳过 {var_name}（图片读取失败）")
            continue

        # 关键：必须【等比】缩放！非等比（x/y 用不同比例）会让模板变形，
        # 匹配分数从 0.97 掉到 0.6。以高度比例作为统一因子。
        new_w = max(1, round(img.shape[1] * SCALE))
        new_h = max(1, round(img.shape[0] * SCALE))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        out_name = f"{new_name}.png"
        cv2.imwrite(os.path.join(out_dir, out_name), resized)

        imported.append({
            "name": new_name,
            "file": out_name,
            "threshold": info["threshold"],
            "roi_front": oas_roi(info["roi_front"]),
            "roi_back": oas_roi(info["roi_back"]),
            "size": (new_w, new_h),
            "oas_roi_front": info["roi_front"],
            "oas_roi_back": info["roi_back"],
        })
        if verbose:
            print(f"  {var_name:26s} -> {out_name:20s} "
                  f"{img.shape[1]}x{img.shape[0]} -> {new_w}x{new_h}")
    return imported


def print_config(imported):
    """按 config 的 TEMPLATES 格式打印，方便直接粘贴"""
    print("\n=== 换算后的 roi（OAS 1280x720 -> BASE 878x404）===")
    print("注：roi_front 只决定点击落点；roi_back 才是识别搜索区。\n")
    for it in imported:
        print(f'    "{it["name"]}": {{')
        print(f'        "file": "{it["file"]}",')
        print(f'        "threshold": {it["threshold"]},')
        print(f'        "roi_back": {it["roi_back"]},'
              f'     # OAS 原值 {it["oas_roi_back"]}')
        print(f'        "roi_front": {it["roi_front"]},'
              f'    # OAS 原值 {it["oas_roi_front"]}')
        print(f'    }},')


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: python tools/import_oas_assets.py <任务名|all>")
        print("可选任务:")
        for k, v in TASK_ASSETS.items():
            print(f"  {k:12s} -> {'/'.join(v['out_dir'])}  ({len(v['items'])} 项)")
        return

    if not os.path.isdir(OAS_ROOT):
        print(f"错误: 找不到 OAS 项目 {OAS_ROOT}")
        print("请先把 https://github.com/nearaura/OnmyojiAutoScript 克隆到该路径")
        raise SystemExit(1)

    targets = list(TASK_ASSETS) if args[0] == "all" else [args[0]]
    for t in targets:
        imported = import_task(t)
        print_config(imported)
        print(f"\n完成: {t} 导入 {len(imported)} 个模板")

    print("\n提醒：roi 只是换算值，不是实机标定值。首次使用前跑一次标定工具确认。")


if __name__ == "__main__":
    main()
