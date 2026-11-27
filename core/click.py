"""
点击区域规则 —— 精确移植 OAS `module/atom/click.py`

为什么需要这个：
刷御魂时有两类"点空白推进"的动作（跳过胜利动画、关结算界面）。
如果每次都点同一个坐标，是最明显的机器特征。
OAS 的做法是定义若干**安全空白区域**，每次随机选一块、区域内随机取点。

两个类：
- RuleClick        ：一个固定矩形，区域内正态随机取点
- RuleClickExclude ：全屏随机取点，但**避开**指定按钮区域
                     （拒绝采样 / 补集矩形，两种策略 OAS 随机选一种）
"""
import random

from core.random_utils import random_normal_distribution_int, random_rectangle_point

# 与 OAS RuleClickExclude 常量对应，本项目基准分辨率
SCREEN_WIDTH = 878
SCREEN_HEIGHT = 404


class RuleClick:
    """一个可点击区域（坐标口径 x, y, w, h）"""

    def __init__(self, roi_front, roi_back=None, name="click"):
        self.roi_front = roi_front
        self.roi_back = roi_back or roi_front
        self.name = name

    # ---- OAS 同名方法 ----
    def coord(self):
        """在 roi_front 内正态随机取点（OAS RuleClick.coord）"""
        return random_rectangle_point(self.roi_front)

    def coord_more(self):
        """在 roi_back 内正态随机取点（OAS RuleClick.coord_more）"""
        return random_rectangle_point(self.roi_back)

    @property
    def center(self):
        x, y, w, h = self.roi_front
        return x + w // 2, y + h // 2

    def __str__(self):
        return f"{self.name}{self.roi_front}"

    __repr__ = __str__


class RuleClickExclude(RuleClick):
    """
    全屏随机点击，但避开传入的按钮区域。

    两种取样策略（OAS 在初始化时随机选一种，让行为本身也不固定）：
    - rejection  : 全屏随机撒点，落在排除区就重撒，撒 max_attempts 次还没成功就退化为补集
    - complement : 预先算出"可点矩形集合"，按面积加权随机选一个矩形再取点

    两种坐标分布（同样随机选）：
    - uniform : 完全均匀
    - normal  : 正态聚中
    """

    SCREEN_WIDTH = SCREEN_WIDTH
    SCREEN_HEIGHT = SCREEN_HEIGHT

    def __init__(self, clicks, strategy=None, distribution=None,
                 max_attempts=100, name="exclude_random_click"):
        super().__init__((0, 0, self.SCREEN_WIDTH, self.SCREEN_HEIGHT),
                         (0, 0, self.SCREEN_WIDTH, self.SCREEN_HEIGHT), name=name)
        if strategy is None:
            strategy = random.choice(("rejection", "complement"))
        if distribution is None:
            distribution = random.choice(("uniform", "normal"))
        if strategy not in ("rejection", "complement"):
            raise ValueError("strategy must be 'rejection' or 'complement'")
        if distribution not in ("uniform", "normal"):
            raise ValueError("distribution must be 'uniform' or 'normal'")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        if isinstance(clicks, RuleClick):
            clicks = (clicks,)
        self.clicks = tuple(clicks)
        if not all(isinstance(c, RuleClick) for c in self.clicks):
            raise TypeError("clicks must contain RuleClick instances")

        self.strategy = strategy
        self.distribution = distribution
        self.max_attempts = max_attempts
        self._excluded = tuple(
            rect for c in self.clicks
            for rect in (self._clip_roi(c.roi_back),)
            if rect is not None
        )
        self._allowed = self._build_complement()

    # ---- 内部工具 ----
    @classmethod
    def _clip_roi(cls, roi):
        x, y, w, h = roi
        left, top = max(0, x), max(0, y)
        right, bottom = min(cls.SCREEN_WIDTH, x + w), min(cls.SCREEN_HEIGHT, y + h)
        if left >= right or top >= bottom:
            return None
        return left, top, right - left, bottom - top

    def _is_excluded(self, x, y):
        return any(left <= x < left + w and top <= y < top + h
                   for left, top, w, h in self._excluded)

    def _build_complement(self):
        """用坐标切分法算出所有不被排除的矩形（OAS 原算法）"""
        x_edges = {0, self.SCREEN_WIDTH}
        y_edges = {0, self.SCREEN_HEIGHT}
        for x, y, w, h in self._excluded:
            x_edges.update((x, x + w))
            y_edges.update((y, y + h))

        x_edges, y_edges = sorted(x_edges), sorted(y_edges)
        allowed = []
        for x1, x2 in zip(x_edges, x_edges[1:]):
            for y1, y2 in zip(y_edges, y_edges[1:]):
                if not self._is_excluded(x1, y1):
                    allowed.append((x1, y1, x2 - x1, y2 - y1))
        return allowed

    def _sample_axis(self, start, size, distribution):
        if size <= 1:
            return start
        if distribution == "uniform":
            return random.randrange(start, start + size)
        return max(start, min(start + size - 1,
                              random_normal_distribution_int(start, start + size)))

    def _sample_point(self, rect, distribution):
        x, y, w, h = rect
        return (self._sample_axis(x, w, distribution),
                self._sample_axis(y, h, distribution))

    def _coord_rejection(self, distribution):
        for _ in range(self.max_attempts):
            point = self._sample_point(
                (0, 0, self.SCREEN_WIDTH, self.SCREEN_HEIGHT), distribution)
            if not self._is_excluded(*point):
                return point
        return self._coord_complement(distribution)

    def _coord_complement(self, distribution):
        if not self._allowed:
            raise ValueError("Excluded regions cover the whole screen")
        total = sum(w * h for _, _, w, h in self._allowed)
        offset = random.randrange(total)
        for rect in self._allowed:
            area = rect[2] * rect[3]
            if offset < area:
                return self._sample_point(rect, distribution)
            offset -= area
        # 理论不可达，兜底取第一个可点矩形
        return self._sample_point(self._allowed[0], distribution)

    # ---- 对外 ----
    def coord(self):
        if self.strategy == "rejection":
            return self._coord_rejection(self.distribution)
        return self._coord_complement(self.distribution)

    def __str__(self):
        names = ', '.join(c.name for c in self.clicks) or 'none'
        return (f"{self.name}(strategy={self.strategy}, "
                f"distribution={self.distribution}, exclude=[{names}])")

    __repr__ = __str__
