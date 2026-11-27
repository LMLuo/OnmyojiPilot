"""
坐标口径换算：OAS 的 1280x720  ->  本项目的 878x404

为什么需要这一层
----------------
OAS 所有 `RuleImage` / `RuleClick` 的 roi 都是按 **1280x720** 标定的。
我们的基准画面是 **878x404**。如果要把 OAS 里现成的 roi 直接拿来用
（例如新做一个副本模块），必须过一次换算，不能照抄数字。

换算关系是怎么确定的
--------------------
不是猜的，是三条实测命中点反推 + 几何自洽验证出来的：

    缩放比 S  = BASE_H / SOURCE_H = 404 / 720  = 0.561111
    内容区宽  = 1280 * S          = 718.22
    水平留边  = (878 - 718.22)/2  = 79.889      <- 每侧一条黑边

关键验证：718.22 + 79.889 * 2 = **878.00**，正好等于 BASE_W。
说明 OAS 的 16:9 画面在 ZenLink 里是"按高度缩放到 404，左右各留一条黑边"。

    实测校验（reward / fire_btn / soul_entry 三个已知命中点）：
        reward      预测 435.1  实测 435    差 -0.1
        fire_btn    预测 746.5  实测 768    差 +21.5   <- roi 当初手调过，见 config.py 注释
        soul_entry  预测 131.5  实测 163    差 +31.5   <- 同上
    纵向三个点误差都在 6px 内。
    两个偏差大的都是"人工调过 roi"的项，不是换算错，所以默认可信。

这也解释了为什么 `tools/import_oas_assets.py` 统一用 S 缩放模板是对的：
游戏内容就是按 S 渲染的，模板跟着同比例缩放才能对上。

如果实机标定发现偏差
--------------------
跑 `tools/calibrate_roi.py`，它会用模板全图匹配量出真实位置，
给出修正后的 (S, OX) 并打印。调好后改下面的 DEFAULT_* 即可，
所有 roi 会同时归位 —— 这也是把换算集中在函数里的原因。
"""
import os
import json
import logging

log = logging.getLogger("OnmyojiPilot")

# OAS 标定口径
SOURCE_W = 1280
SOURCE_H = 720

# 本项目基准口径（与 core/matcher.py、core/window.py 保持一致）
BASE_W = 878
BASE_H = 404

# 默认换算参数（见模块注释的推导过程）
DEFAULT_SCALE = BASE_H / SOURCE_H                      # 0.561111
DEFAULT_OFFSET_X = (BASE_W - SOURCE_W * DEFAULT_SCALE) / 2.0   # 79.889
DEFAULT_OFFSET_Y = 0.0

# 实机标定结果落盘位置（不写进 git，属于本机数据）
CALIB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "history", "coord_calibration.json")


class OasToBase:
    """
    OAS(1280x720) -> BASE(878x404) 的仿射换算器。

    默认用推导出的参数；如果 `history/coord_calibration.json` 存在
    （由标定工具写入），则优先使用标定值。

    注意：**模板图本身不需要换算**，导入时已经按 S 等比缩放过。
    这里只换算 roi 坐标。
    """

    def __init__(self, scale=None, offset_x=None, offset_y=None,
                 source_w=SOURCE_W, source_h=SOURCE_H, use_calibration=True):
        self.source_w = source_w
        self.source_h = source_h
        self.scale = DEFAULT_SCALE if scale is None else float(scale)
        self.offset_x = DEFAULT_OFFSET_X if offset_x is None else float(offset_x)
        self.offset_y = DEFAULT_OFFSET_Y if offset_y is None else float(offset_y)
        self.calibrated = False
        if use_calibration and scale is None:
            self._load_calibration()

    # ---------- 标定结果 ----------
    def _load_calibration(self):
        if not os.path.exists(CALIB_PATH):
            return
        try:
            with open(CALIB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.scale = float(data["scale"])
            self.offset_x = float(data["offset_x"])
            self.offset_y = float(data.get("offset_y", 0.0))
            self.calibrated = True
            log.info("已载入坐标标定: S=%.6f OX=%.3f OY=%.3f",
                     self.scale, self.offset_x, self.offset_y)
        except Exception as e:                     # 标定文件坏了不能拖垮任务
            log.warning("坐标标定文件读取失败，改用默认参数: %s", e)

    def save_calibration(self, scale=None, offset_x=None, offset_y=None):
        """把标定结果写入 history/coord_calibration.json"""
        if scale is not None:
            self.scale = float(scale)
        if offset_x is not None:
            self.offset_x = float(offset_x)
        if offset_y is not None:
            self.offset_y = float(offset_y)
        os.makedirs(os.path.dirname(CALIB_PATH), exist_ok=True)
        with open(CALIB_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "scale": round(self.scale, 6),
                "offset_x": round(self.offset_x, 3),
                "offset_y": round(self.offset_y, 3),
                "source": f"{self.source_w}x{self.source_h}",
                "base": f"{BASE_W}x{BASE_H}",
            }, f, ensure_ascii=False, indent=2)
        self.calibrated = True
        return CALIB_PATH

    # ---------- 换算 ----------
    def point(self, x, y):
        """OAS 单点 -> BASE 单点（返回浮点，调用方自行 round）"""
        return (self.offset_x + float(x) * self.scale,
                self.offset_y + float(y) * self.scale)

    def roi(self, roi):
        """
        OAS roi (x, y, w, h) -> BASE roi (x, y, w, h)（整数）

        None 原样返回（表示"全图搜索"，这是 matcher 里合法的语义）。
        """
        if roi is None:
            return None
        x, y, w, h = roi
        bx, by = self.point(x, y)
        bw = float(w) * self.scale
        bh = float(h) * self.scale
        return (int(round(bx)), int(round(by)),
                max(8, int(round(bw))), max(8, int(round(bh))))

    def rect_from_center(self, cx, cy, w, h):
        """
        以 OAS 中心点 + BASE 尺寸反推 BASE roi。

        标定工具量到的是"元素中心"，而配置要的是左上角，
        这个函数避免到处手写 "- w/2"。
        """
        bx, by = self.point(cx, cy)
        return (int(round(bx - w / 2.0)), int(round(by - h / 2.0)),
                int(round(w)), int(round(h)))

    def __repr__(self):
        return (f"OasToBase(S={self.scale:.6f}, OX={self.offset_x:.3f}, "
                f"OY={self.offset_y:.3f}, calibrated={self.calibrated})")


# 模块级默认实例：绝大多数地方直接用这个
DEFAULT = OasToBase()


def oas_roi(roi):
    """快捷函数：OAS roi -> BASE roi（用默认实例）"""
    return DEFAULT.roi(roi)


def oas_point(x, y):
    """快捷函数：OAS 点 -> BASE 点（整数）"""
    bx, by = DEFAULT.point(x, y)
    return int(round(bx)), int(round(by))
