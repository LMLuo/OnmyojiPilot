"""
模板匹配内核 —— 移植 OAS 的 RuleImage 机制

对照 OAS `module/atom/image.py` 实现的关键能力：
1. roi_back：限定搜索区域，大幅提速 + 减少误匹配（OAS 每个模板都有）
2. roi_front：点击区域，点击时在区域内随机扰动（防机械点击）
3. 退化模板检测（纯色模板会误匹配，OAS 有 _template_is_degenerate）
4. 匹配结果清洗（nan/inf，OAS 有 _sanitize_match_result）
5. 多尺度匹配（应对分辨率不一致）

坐标口径：所有 roi 都按基准分辨率标定。
"""
import os
import cv2
import numpy as np
import logging

from core.random_utils import random_normal_distribution_int
from core.window import fit_transform

log = logging.getLogger("OnmyojiPilot")


def _is_degenerate(mat):
    """OAS _template_is_degenerate：纯色/无方差模板会误匹配，直接判不匹配"""
    if mat is None or mat.size == 0:
        return True
    return float(mat.std()) < 1e-3


def _sanitize(result):
    """OAS _sanitize_match_result：清理 nan/inf，避免 minMaxLoc 异常"""
    return np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)


class Template:
    # 标定基准分辨率（所有 roi 与模板都按此口径记录）
    BASE_W = 878
    BASE_H = 404

    def __init__(self, name, path, threshold=0.85, roi_back=None, roi_front=None,
                 scales=None, verify_binary=0.0):
        self.name = name
        self.path = path
        self.threshold = threshold
        self.roi_back = roi_back      # (x,y,w,h) 搜索区域（基准坐标）
        self.roi_front = roi_front    # (x,y,w,h) 点击区域（基准坐标）
        # 相对"当前窗口/基准"缩放比的微调档位（±10%，覆盖 5 档）
        # 窗口尺寸每次启动都可能变（实测 739x340 / 878x404 / 999x458 都出现过），
        # 模板必须跟着窗口一起缩放，否则 fire_btn 从 0.953 掉到 0.206 直接失配。
        self.scales = scales or [0.90, 0.95, 1.00, 1.05, 1.10]
        # 二值亮字结构校验阈值（0 = 关闭）
        # 见 _verify_binary 注释：TM_CCOEFF_NORMED 对"近黑背景 + 少量亮斑"的模板
        # 判别力不足，窗口变小后可匹配位置数骤减，随便一个亮块就能冲到 0.65+。
        self.verify_binary = verify_binary
        self.image = None
        self._warned_aspect = False
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            log.warning("模板不存在: %s", self.path)
            return
        self.image = cv2.imread(self.path, cv2.IMREAD_COLOR)
        if self.image is None:
            log.warning("模板读取失败: %s", self.path)

    def _fits(self, screen):
        """
        返回坐标映射候选列表 [(sx, sy, ox, oy), ...]。

        只用 **contain**（等比缩放 + 居中黑边）：这是 ZenLink 保持手机画面比例时的显示方式。
        实测窗口 739x340 / 878x404 / 999x458 / 1130x520 的视频子窗口宽高比
        全部稳定在 2.17 左右（= 手机 19.5:9），说明它是保持比例显示的。

        刻意**不做**"非等比拉伸"的兜底：
        万一画面真被拉伸，用拉伸映射也能匹配上，但算出来的点击位置会偏，
        比干净地失败更危险。这里只在宽高比异常时告警，让问题暴露出来。

        候选列表长度 >1 是为将来扩展留的口子，目前只返回 1 个。
        """
        sw, sh = screen.shape[1], screen.shape[0]
        scale, ox, oy = fit_transform(sw, sh, self.BASE_W, self.BASE_H)

        # 宽高比明显偏离基准时告警（只报一次），提示窗口形状异常
        sx, sy = sw / self.BASE_W, sh / self.BASE_H
        if max(sx, sy) > 0 and abs(sx - sy) / max(sx, sy) > 0.08 and not self._warned_aspect:
            self._warned_aspect = True
            log.warning(
                "窗口宽高比异常（%dx%d，偏离标准 %.0f%%）：如果画面被拉伸而不是加黑边，"
                "识别会不准。建议把 ZenLink 窗口拖成接近 2.17:1 的形状。",
                sw, sh, abs(sx - sy) / max(sx, sy) * 100)

        return [(scale, scale, ox, oy)]

    def _scaled_roi(self, roi, screen, fit=None):
        """把基准口径的 roi 映射到当前画面（等比缩放 + 黑边偏移）"""
        if roi is None:
            return None
        sx, sy, ox, oy = fit or self._fits(screen)[0]
        x, y, w, h = roi
        return (round(ox + x * sx), round(oy + y * sy),
                max(8, round(w * sx)), max(8, round(h * sy)))

    def _crop(self, screen, roi):
        """照搬 OAS corp()：按 roi 裁剪搜索区域"""
        if roi is None:
            return screen, 0, 0
        x, y, w, h = roi
        h_img, w_img = screen.shape[:2]
        x2, y2 = min(x + w, w_img), min(y + h, h_img)
        return screen[y:y2, x:x2], x, y

    def match(self, screen_bgr):
        """
        返回 (命中, 分数, 中心坐标)

        严格对齐 OAS `RuleImage.match`：**只在 roi_back 内搜索**。
        早期版本在 roi 未命中时会回退全图搜索，实测会在错误界面产生高误匹配
        （例：御魂副本页上 moans_layer 误报 0.940），因此取消该兜底。
        没有 roi_back 的模板（全屏通用标志）才走全图多尺度搜索。

        窗口尺寸任意变化都能适配：坐标映射见 _fits()，模板同步缩放。
        """
        if self.image is None or _is_degenerate(self.image):
            return False, 0.0, None

        best_score = 0.0
        for fit in self._fits(screen_bgr):
            if self.roi_back:
                roi = self._scaled_roi(self.roi_back, screen_bgr, fit)
                search, ox, oy = self._crop(screen_bgr, roi)
            else:
                search, ox, oy = screen_bgr, 0, 0
            # 不做尺寸预判：模板按映射比例缩放，能否放进 search 由 _match_in
            # 逐档判断（窗口比基准小时，拿原尺寸预判会误伤）
            ok, score, pos = self._match_in(search, ox, oy, fit[0], fit[1])
            if ok:
                return True, score, pos
            best_score = max(best_score, score)
        return False, best_score, None

    def _match_in(self, search, ox, oy, sx=1.0, sy=None, scales=None):
        """在给定图像区域内多尺度匹配（尺度 = 映射比例 × 微调档位）"""
        if sy is None:
            sy = sx
        best_score, best_pos, best_size = -1.0, None, None
        base_h, base_w = self.image.shape[:2]

        for step in (scales or self.scales):
            w = max(8, round(base_w * sx * step))
            h = max(8, round(base_h * sy * step))
            if w > search.shape[1] or h > search.shape[0]:
                continue
            tpl = cv2.resize(self.image, (w, h), interpolation=cv2.INTER_AREA)
            res = _sanitize(cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED))
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best_score:
                best_score, best_pos, best_size = max_val, max_loc, (w, h)

        if best_pos is None or best_score < self.threshold:
            return False, max(best_score, 0.0), None

        # 二次校验：CCOEFF 过了还不够，再看亮部结构是否真的对齐
        if self.verify_binary > 0:
            px, py = best_pos
            tw, th = best_size
            patch = search[py:py + th, px:px + tw]
            tpl = cv2.resize(self.image, (tw, th), interpolation=cv2.INTER_AREA)
            bin_score = self._binary_agreement(tpl, patch)
            if bin_score < self.verify_binary:
                log.debug("%s CCOEFF=%.3f 但亮部结构不符(%.3f<%.2f)，判不命中",
                          self.name, best_score, bin_score, self.verify_binary)
                return False, best_score, None

        cx = ox + best_pos[0] + best_size[0] // 2
        cy = oy + best_pos[1] + best_size[1] // 2
        return True, best_score, (cx, cy)

    @staticmethod
    def _binary_agreement(tpl_bgr, patch_bgr):
        """
        亮部（文字/描边）结构一致性：把两张图各自按自身亮度自适应二值化，
        再算二值掩码的相关系数。

        为什么需要这一步：
            本项目的关键模板（"点击屏幕继续"/"挑战"）都是**近黑背景 + 细亮字**。
            TM_CCOEFF_NORMED 会先减去均值再除以标准差，背景极暗时方差很小，
            除数趋近 0，任意一块亮斑（例如战斗界面的角色头像）都能把分数顶上去 ——
            实测同一模板在战斗帧上能到 0.66~0.84，而真掉落页是 0.99，
            窗口缩到 540x249 时两者区间直接重叠，单靠阈值无法分开。
            二值掩码相关性只关心"亮的地方是不是同一批像素"，
            御魂页/战斗帧上实测 0.00~0.27，真掉落页 0.85~0.97，区分得很干净。
        """
        gt = cv2.cvtColor(tpl_bgr, cv2.COLOR_BGR2GRAY)
        gp = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
        if gt.size == 0 or gp.size == 0:
            return 0.0
        bt = (gt > float(gt.mean()) + 1.2 * float(gt.std())).astype(np.uint8)
        bp = (gp > float(gp.mean()) + 1.2 * float(gp.std())).astype(np.uint8)
        # 允许 1px 错位：小窗口下文字只有 6px 高，缩放插值会让笔画整体偏半像素，
        # 不做容差的话真掉落页在 540x249 也只得 0.59（离阈值太近）。
        # 膨胀 3x3 后实测：正样本最低 0.589->0.797，负样本最高 0.302->0.275，
        # 正负间距从 0.288 拉到 0.522，判别余量翻倍。
        kernel = np.ones((3, 3), np.uint8)
        bt = cv2.dilate(bt, kernel)
        bp = cv2.dilate(bp, kernel)
        bt = bt.astype(np.float32)
        bp = bp.astype(np.float32)
        # 掩码无变化（全亮/全暗）时相关系数没有意义
        if bt.std() < 1e-6 or bp.std() < 1e-6:
            return 0.0
        res = cv2.matchTemplate(bp, bt, cv2.TM_CCOEFF_NORMED)
        val = float(res[0, 0])
        return 0.0 if not np.isfinite(val) else max(0.0, val)

    def click_point(self, hit_center=None, screen=None):
        """
        返回点击坐标，**坐标口径是基准分辨率（878x404）**。

        为什么必须用基准口径：
        WindowDevice.click() 内部会统一做一次 基准->窗口 的换算（含黑边偏移）。
        如果这里先换成窗口坐标，device 再换一次就是"双倍放大" ——
        878x404 下缩放比是 1.0 看不出来，但窗口变 999x458 时
        点击会算到 (1030,450)，直接落到窗口外面。
        所以全项目统一：内部一律用基准坐标，只在 device.click 出口换算一次。

        选点逻辑仍照搬 OAS `RuleImage.coord()`：
            x + random_normal_distribution_int(0, w), y + random_normal_distribution_int(0, h)
        正态分布让落点聚在中心、偶尔偏一点，且严格截断在 roi_front 内。
        """
        if self.roi_front:
            x, y, w, h = self.roi_front
            return (x + random_normal_distribution_int(0, w),
                    y + random_normal_distribution_int(0, h))
        return hit_center


class Matcher:
    def __init__(self, template_dir):
        self.template_dir = template_dir
        self.templates = {}

    def add(self, name, filename, threshold=0.85, roi_back=None, roi_front=None,
            verify_binary=0.0):
        path = os.path.join(self.template_dir, filename)
        self.templates[name] = Template(name, path, threshold, roi_back, roi_front,
                                        verify_binary=verify_binary)
        return self.templates[name]

    def load_config(self, config):
        for name, cfg in config.items():
            self.add(name, cfg["file"], cfg.get("threshold", 0.85),
                     cfg.get("roi_back"), cfg.get("roi_front"),
                     cfg.get("verify_binary", 0.0))

    def find(self, name, screen_bgr):
        t = self.templates.get(name)
        if not t:
            return False, 0.0, None
        return t.match(screen_bgr)

    def find_any(self, names, screen_bgr):
        """任一命中即返回（OAS any_of 思路）"""
        best = (None, 0.0, None)
        for name in names:
            ok, score, pos = self.find(name, screen_bgr)
            if ok:
                return name, score, pos
            if score > best[1]:
                best = (name, score, None)
        return best
