"""
任务基类 —— 移植 OAS BaseTask 的核心机制

对照 OAS `tasks/base_task.py` + `module/device/device.py` + `module/atom/click.py`：
1. interval 定时器：每个识别目标独立计时，避免高频重复点击
2. 重复点击检测（OAS click_record_check）：同一按钮点 10 次 / 两按钮各 6 次 -> 报错
3. 卡死检测（OAS stuck_record_check）：长时间无进展 -> 报错
4. 点击坐标随机扰动（OAS RuleImage.coord）：正态分布，非均匀分布
5. **随机时长（OAS ensure_time）**：所有等待/按压时长都是区间内正态随机
6. **多区域随机点击（OAS C_WIN_1/2/3 + random.choice）**：空白推进不点固定坐标
7. 全屏随机点击避开按钮（OAS RuleClickExclude）
"""
import time
import random
import logging
from collections import deque

import cv2

from core.window import WindowDevice
from core.matcher import Matcher
from core.click import RuleClick, RuleClickExclude
from core.random_utils import ensure_time

log = logging.getLogger("OnmyojiPilot")


class TaskEnd(Exception):
    """任务正常结束"""
    pass


class ScriptStopped(Exception):
    """用户手动停止"""
    pass


class GameStuckError(Exception):
    """长时间无进展（OAS GameStuckError）"""
    pass


class GameTooManyClickError(Exception):
    """同一按钮点击过多（OAS GameTooManyClickError）"""
    pass


class Timer:
    """照搬 OAS module/base/timer.py 的最小实现"""

    def __init__(self, limit=0):
        self.limit = limit
        self.start_time = time.time()

    def start(self):
        self.start_time = time.time()
        return self

    def reset(self):
        self.start_time = time.time()

    def reached(self):
        return time.time() - self.start_time >= self.limit

    def wait(self):
        remain = self.limit - (time.time() - self.start_time)
        if remain > 0:
            time.sleep(remain)


class BaseTask:
    # OAS 默认阈值
    CLICK_RECORD_SAME_MAX = 10     # 同一按钮连续点击数上限
    CLICK_RECORD_PAIR_MAX = 6      # 两个按钮各点次数上限
    STUCK_TIMEOUT = 120            # 无进展超时（秒）

    def __init__(self, device: WindowDevice, matcher: Matcher,
                 click_regions=None, close_regions=None, timing=None,
                 settle_exclude=None):
        self.device = device
        self.matcher = matcher
        self.image = None
        self.bgr = None
        self.stop_flag = False
        # OAS interval_timer：每目标独立定时器
        self.interval_timer = {}
        # OAS click_record：滑动窗口记录点击历史
        self.click_record = deque(maxlen=20)
        # OAS stuck_timer
        self.stuck_timer = Timer(self.STUCK_TIMEOUT).start()

        # ---- 防机械点击 ----
        # 随机时长的区间表（照搬 OAS ensure_time 用法）
        self.timing = timing or {}
        # 空白安全区（OAS C_WIN_1/2/3 的等价物）
        self.click_regions = {
            name: RuleClick(roi, name=name)
            for name, roi in (click_regions or {}).items()
        }
        # 结算界面可点区
        self.close_regions = {
            name: RuleClick(roi, name=name)
            for name, roi in (close_regions or {}).items()
        }
        # 结算/翻牌界面必须避开矩形（道具图标，点了会弹物品描述浮层）
        self.settle_exclude = [tuple(r) for r in (settle_exclude or [])]

    # ---------- 随机时长（照搬 OAS ensure_time 用法）----------
    def sleep(self, second):
        """
        随机等待。second 可以是数字，也可以是 (下限, 上限) 元组。
        传元组时用 ensure_time 取正态分布值 —— 每次等待时长都不同。
        """
        time.sleep(ensure_time(second))

    def rand_duration(self, key):
        """取 timing 表中某类动作的随机时长"""
        return ensure_time(self.timing.get(key, 0.1))

    # ---------- 随机区域点击（照搬 OAS C_WIN + random.choice 思路）----------
    def click_random_region(self, regions=None, control_name="blank", repeat_check=True):
        """
        从候选区域里**随机选一块**，再在区域内**正态随机取点**点击。

        Args:
            regions: RuleClick 的 dict；默认用 self.click_regions
            control_name: 记录到点击历史的名字
            repeat_check: 是否参与"同一按钮重复点击"检测。
                "跳过动画的空白点击"应传 False —— 它没有按钮身份，
                每次位置都不同，按按钮计数必然误报（实测 4 块区域
                随机点击会在 20 次窗口内触发 6+6 平手规则）。
                真正卡死由 battle_timeout / stuck_record_check 兜底。

        Returns:
            (x, y) 或 None（没有可用区域时）
        """
        pool = regions if regions is not None else self.click_regions
        if not pool:
            return None
        target = random.choice(list(pool.values()))
        x, y = target.coord()
        # 名字带上区域名，便于日志排查（OAS 的 C_WIN_1/2/3 就是按区域分名的）
        self.click(x, y, control_name=f"{control_name}:{target.name}",
                   repeat_check=repeat_check)
        return x, y

    def click_exclude(self, exclude_names, control_name="exclude_blank"):
        """
        全屏随机点，但避开指定模板/区域的按钮位置（OAS RuleClickExclude）。

        Args:
            exclude_names: 需要避开的模板名，或 (x,y,w,h) 元组列表
        """
        boxes = []
        for item in exclude_names:
            if isinstance(item, str):
                tpl = self.matcher.templates.get(item)
                if tpl is not None and tpl.roi_front:
                    boxes.append(RuleClick(tpl.roi_front, name=item))
            else:
                boxes.append(RuleClick(item, name="box"))
        try:
            rule = RuleClickExclude(boxes, name=control_name)
            x, y = rule.coord()
        except ValueError:
            # 排除区覆盖全屏时退化为随机区域点击
            return self.click_random_region(control_name=control_name)
        # 全屏随机点：不做"同一按钮重复"检查（名字相同但位置每次都不同）
        self.click(x, y, control_name=control_name, repeat_check=False)
        return x, y

    def click_settle_blank(self, control_name="settle"):
        """
        结算/翻牌界面推进：在**避开所有道具图标**的空白处随机点击。

        等价于 OAS 的 reward_exclude_click（battle_wait.py:1179-1181）。
        为什么不能直接点 reward 图标：点中道具会弹出物品描述浮层，
        界面不前进但图标还在，于是被判定为"还没点完"而连点，
        10 次后触发 GameTooManyClickError。实测已复现。

        Returns:
            (x, y) 或 None
        """
        if not self.settle_exclude:
            return self.click_random_region(control_name=control_name, repeat_check=False)
        return self.click_exclude(self.settle_exclude, control_name=control_name)

    # ---------- 截图 ----------
    def screenshot(self):
        rgb = self.device.screenshot()
        self.image = rgb
        self.bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        return rgb

    def screenshot_fresh(self):
        """
        截图，返回该帧是否为**新鲜帧**。

        PrintWindow 在战斗加载/界面切换瞬间抓不到图，此时返回的是上一帧缓存。
        缓存帧上还留着旧界面（例如挑战按钮），拿它做判断会导致"按钮明明没了还在等"。
        """
        self.screenshot()
        return self.device.frame_fresh

    # ---------- 识别（含 interval 节流，照搬 OAS appear）----------
    def appear(self, name, interval=None):
        if interval:
            timer = self.interval_timer.get(name)
            if timer is None or timer.limit != interval:
                timer = Timer(interval)
                self.interval_timer[name] = timer
            if not timer.reached():
                return False, 0.0, None

        ok, score, pos = self.matcher.find(name, self.bgr)
        if ok and interval:
            self.interval_timer[name].reset()
        return ok, score, pos

    def appear_any(self, names, interval=None):
        for name in names:
            ok, score, pos = self.appear(name, interval=interval)
            if ok:
                return name, score, pos
        return None, 0.0, None

    def appear_then_click(self, name, interval=0.6):
        """识别到则点击（OAS appear_then_click）"""
        ok, score, pos = self.appear(name, interval=interval)
        if not ok:
            return False
        target = self.matcher.templates.get(name)
        x, y = target.click_point(pos) if target else pos
        self.click(x, y, control_name=name)
        return True

    # ---------- 点击（含防卡死，照搬 OAS handle_control_check）----------
    def click(self, x, y, control_name=None, duration=None, repeat_check=True):
        """
        duration=None 时用 TIMING['click_dur'] 的随机区间，
        避免每次按压时长完全一致（固定按压时长是很强的机器特征）。

        repeat_check=False 用于"全屏随机空白点击"这类场景：
        它没有按钮身份（每次位置都不同），既不参与"同一按钮重复点击"的
        记录与检查，也只保留清卡死计时。
        （OAS 对应 `click(control_check=False)`，里面对 handle_control_check
        是整体跳过的，不会写进 click_record）
        """
        if duration is None:
            duration = self.rand_duration("click_dur")
        self.device.click(x, y, duration=duration)
        self.handle_control_check(control_name or f"({x},{y})", repeat_check=repeat_check)

    def handle_control_check(self, button, repeat_check=True):
        """
        记录点击 + 清卡死计时 + 检查重复点击。

        注意 `repeat_check=False` 的处理，这里踩过一个必现 bug：
        原先的写法是"**记录下来、只是不做检查**"，看着无害，其实是定时炸弹 ——
        结算界面的空白点击（数量多、名字都叫 settle）会静默堆进 click_record，
        没人检查就一直攒着；等到下一轮 `enter_battle` 点「挑战」时，
        那次是带检查的，于是一并计数，直接 >=10 报
        GameTooManyClickError。实测在传承试炼跑到第 5 轮必炸
        （17:28:23 那次的日志就是）。

        正确语义照 OAS `module/device/control.py:63 click(control_check=False)`：
        **既不记录、也不检查**，只保留清卡死计时。OAS 里随机空白点击
        （multi_click）走的就是这条路。
        """
        self.stuck_timer.reset()
        if not repeat_check:
            return
        self.click_record.append(str(button))
        self.click_record_check()

    def click_record_clear(self):
        """
        清空点击历史（照搬 OAS `device.click_record_clear()`）。

        必须在**每一轮战斗开始**时调用，原因见 handle_control_check 的注释：
        click_record 是跨轮累积的，不清就会在第 5~10 轮之间误报防卡死。
        OAS 在 `battle_wait()` 开头和 `general_battle.py:172` 都做了这一步。
        """
        self.click_record.clear()

    def click_record_check(self):
        """OAS click_record_check：同按钮 10 次 / 两按钮各 6 次 -> 报错"""
        count = {}
        for key in self.click_record:
            count[key] = count.get(key, 0) + 1
        if not count:
            return
        ranked = sorted(count.items(), key=lambda kv: kv[1], reverse=True)
        if ranked[0][1] >= self.CLICK_RECORD_SAME_MAX:
            self.click_record.clear()
            raise GameTooManyClickError(f"同一按钮点击过多: {ranked[0][0]}")
        if len(ranked) >= 2 and ranked[0][1] >= self.CLICK_RECORD_PAIR_MAX \
                and ranked[1][1] >= self.CLICK_RECORD_PAIR_MAX:
            self.click_record.clear()
            raise GameTooManyClickError(f"两个按钮反复点击: {ranked[0][0]} / {ranked[1][0]}")

    def stuck_record_check(self):
        """OAS stuck_record_check：长时间无进展 -> 报错"""
        if self.stuck_timer.reached():
            self.stuck_timer.reset()
            raise GameStuckError(f"{self.STUCK_TIMEOUT}s 内无有效进展")

    # ---------- 等待 ----------
    def wait_until(self, name, timeout=30, interval=None):
        start = time.time()
        while time.time() - start < timeout:
            self.check_stop()
            if not self.screenshot_fresh():
                continue   # 缓存帧不做判断
            ok, score, pos = self.appear(name)
            if ok:
                return pos
            self.sleep(interval or self.timing.get("wait_poll", 0.5))
        return None

    def wait_until_gone(self, name, timeout=30, interval=None):
        start = time.time()
        while time.time() - start < timeout:
            self.check_stop()
            if not self.screenshot_fresh():
                continue   # 缓存帧不做判断
            if not self.appear(name)[0]:
                return True
            self.sleep(interval or self.timing.get("wait_poll", 0.5))
        return False

    def check_stop(self):
        if self.stop_flag:
            raise ScriptStopped()
