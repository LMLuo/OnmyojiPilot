"""
英杰试炼 - 鬼兵演武（源赖光的经验本）

起点：停在「英杰试炼」主页面，或已经停在「鬼兵演武」页面
循环：点挑战 → 自动战斗 → 结算推进 → 回到鬼兵演武 → 重复

和御魂模块（tasks/orochi）的关系
--------------------------------
战斗判定、防卡死、结算推进这套骨架是同构的，但**不能直接继承 OrochiTask**：
两者的页面锚点不同（御魂认 fire_btn，这里认 hero_fire），
鬼兵演武是经验本、结算界面可能根本没有掉落图标。
所以这里重写一份，逻辑分支按实测调整。

设计对照 OAS `tasks/HeroTest/script_task.py`：
- 起点判断 + 进入子玩法     <- switch_hero / goto_page
- 点挑战并确认进战斗        <- enter_battle
- 战斗等待 + 结算推进       <- battle_wait + run_general_battle
- 防卡死                    <- BaseTask 的 click_record / stuck_record

与 OAS 的差异（有意为之）：
- OAS 的「挑战」按钮用 OCR 识别文字（O_FIRE）。我们没有 OCR，
  改用模板匹配 `hero_fire.png`（OAS 里也有对应的 I_BATTLE 模板图）。
- OAS 会切英杰（源赖光/藤原道长）。本阶段**不切**，
  假定你已经在源赖光这一侧；如果页面标志对不上会明确报错让你手动切。
"""
import time
import logging

from core.base_task import (BaseTask, TaskEnd, ScriptStopped,
                            GameStuckError, GameTooManyClickError)
from core.window import WindowDevice
from core.matcher import Matcher
from tasks.hero_test.task_config import BATTLE_FLAGS

log = logging.getLogger("OnmyojiPilot")


class HeroTestTask(BaseTask):
    """鬼兵演武挂机"""

    def __init__(self, device: WindowDevice, matcher: Matcher, config: dict):
        super().__init__(
            device, matcher,
            click_regions=config.get("click_regions"),
            close_regions=config.get("close_regions"),
            settle_exclude=config.get("settle_exclude"),
            timing=config.get("timing"),
        )
        self.config = config
        self.count = 0
        self.limit = config.get("limit_count", 30)
        self.battle_timeout = config.get("battle_timeout", 180)
        self.start_time = time.time()

        # ---- 运行统计（供控制台界面读取）----
        self.stats = {
            "limit": self.limit,
            "done": 0,
            "failed": 0,
            "retries": 0,
            "durations": [],
            "state": "starting",
            "message": "",
        }
        self.started_at = time.time()
        self.finished_at = None

    def set_state(self, state, message=""):
        self.stats["state"] = state
        self.stats["message"] = message
        if state in ("finished", "stopped", "error"):
            self.finished_at = time.time()

    # ============================================================
    # 主流程
    # ============================================================
    def run(self):
        log.info("=" * 55)
        log.info("英杰试炼 - 鬼兵演武")
        log.info("目标 %d 次，点击方式: %s", self.limit, self.device.click_mode)
        log.info("=" * 55)

        try:
            if not self.ensure_at_start():
                self.set_state("error", "未能进入鬼兵演武页面")
                return
            self.set_state("running")

            while self.count < self.limit:
                self.check_stop()
                self.stuck_record_check()
                log.info("----- 第 %d/%d 次 -----", self.count + 1, self.limit)

                if not self.battle_once():
                    self.stats["failed"] += 1
                    log.warning("本轮失败，尝试恢复界面")
                    if not self.recover_to_gbb_page(timeout=20):
                        log.error("无法回到鬼兵演武页面，任务中止")
                        self.set_state("error", "无法回到鬼兵演武页面，任务中止")
                        break

            else:
                log.info("完成 %d 次，任务结束", self.count)
                self.set_state("finished")

        except ScriptStopped:
            log.info("任务已手动停止，共完成 %d 次", self.count)
            self.set_state("stopped")
        except (GameStuckError, GameTooManyClickError) as e:
            log.error("防卡死机制触发: %s（已完成 %d 次）", e, self.count)
            self.set_state("error", f"防卡死机制触发: {e}")
        except Exception as e:
            log.exception("任务异常: %s", e)
            self.set_state("error", str(e))
            raise

    # ============================================================
    # 起点处理
    # ============================================================
    def is_gbb_page(self):
        """是否在鬼兵演武页面（挑战按钮可见）"""
        return self.appear("hero_fire")[0]

    def is_hero_test_page(self):
        """是否在英杰试炼主页面"""
        return self.appear("hero_test_page")[0]

    def is_in_battle(self):
        """任一战斗标志出现即视为战斗相关界面（照搬 OAS is_in_battle）"""
        for name in BATTLE_FLAGS:
            if self.appear(name)[0]:
                return True
        return False

    def ensure_at_start(self, timeout=15):
        """
        把界面带到「鬼兵演武」页面。
        返回 True=已就位，False=搞不定。
        """
        start = time.time()
        while time.time() - start < timeout:
            self.check_stop()
            if not self.screenshot_fresh():
                continue

            if self.is_gbb_page():
                log.info("已确认起点：鬼兵演武页面")
                return True

            if self.is_hero_test_page():
                log.info("检测到英杰试炼主页面，点击左侧「鬼兵演武」进入")
                if self.appear_then_click("gbb_entry", interval=1.0):
                    self.sleep(self.timing.get("after_click", (0.5, 0.8)))
                    continue

            self.sleep(self.timing.get("wait_poll", 0.5))

        # 区分"投屏断了"和"界面停错了"，避免误导性报错
        self.screenshot()
        if WindowDevice.is_idle_frame(self.image):
            log.error("ZenLink 投屏已断开（画面是 ZenLink 的待机界面）")
            log.error("请先在 ZenLink 里点「开始投屏」恢复画面，再重新运行")
            return False

        log.error("未检测到「挑战」按钮，也没看到「英杰试炼」页面标题")
        log.error("请手动停在【英杰试炼】主页面（或已进入【鬼兵演武】），再重新运行")
        log.error("提示：庭院 → 探索 那一步的漂浮灯笼无法用后台消息点中，需要手动走")
        return False

    def recover_to_gbb_page(self, timeout=20):
        """尝试回到鬼兵演武页面：先点返回，再试一次起点流程"""
        start = time.time()
        while time.time() - start < timeout:
            self.check_stop()
            if not self.screenshot_fresh():
                continue
            if self.is_gbb_page():
                return True
            # 左上角返回箭头：从鬼兵演武回英杰试炼主页面
            if self.appear_then_click("gbb_back", interval=1.2):
                continue
            self.sleep(self.timing.get("wait_poll", 0.5))
        return self.ensure_at_start(timeout=5)

    # ============================================================
    # 单轮战斗
    # ============================================================
    def battle_once(self):
        round_start = time.time()

        # 1. 点挑战 + 确认真的进战斗（OAS enter_battle：最多重试 3~4 次）
        entered = False
        for attempt in range(1, 5):
            ok, score, pos = self.appear("hero_fire")
            if not ok:
                # 挑战按钮不可见：多半已经在战斗/结算里，交给结算等待处理。
                # 绝不在这里乱点（战斗中乱点可能关掉自动战斗）。
                log.info("挑战按钮不可见（第 %d 次检查），转入结算等待", attempt)
                break

            target = self.matcher.templates.get("hero_fire")
            x, y = target.click_point(pos, self.bgr) if target else pos
            log.info("点击挑战 @(%d,%d) score=%.3f（第 %d 次尝试）", x, y, score, attempt)
            self.click(x, y, control_name="hero_fire")
            if attempt > 1:
                self.stats["retries"] += 1

            # 2. 等挑战按钮消失 = 已进入战斗
            t_click = time.time()
            if self.wait_until_gone("hero_fire", timeout=8):
                log.info("已进入战斗（%.1fs）", time.time() - t_click)
                entered = True
                break

            if self.appear("hero_fire")[0]:
                log.warning("挑战按钮仍在，点击未生效，重试（已等 %.1fs）",
                            time.time() - t_click)
                continue
            log.info("挑战按钮已消失，判定已进入战斗（%.1fs）", time.time() - t_click)
            entered = True
            break

        if not entered and self.appear("hero_fire")[0]:
            # 关键：不能继续往下走，否则结算等待会因为"还看得见挑战按钮"
            # 而误判为"已打完回来"，把没打的算成打完了
            log.error("点击挑战未生效（挑战按钮仍在），本轮作废")
            return False

        # 3. 等战斗结束 + 结算推进 + 回到鬼兵演武页面
        if not self.wait_and_return():
            log.warning("未等到回到鬼兵演武页面")
            return False

        self.count += 1
        elapsed = time.time() - round_start
        self.stats["done"] = self.count
        self.stats["durations"].append(round(elapsed, 2))
        log.info("第 %d 次完成，本轮耗时 %.1fs", self.count, elapsed)
        return True

    def wait_and_return(self):
        """
        等战斗结束，在结算界面点空白推进，直到回到鬼兵演武页面（挑战按钮重现）。

        复用御魂模块验证过的三条结论：
        1. 结算界面只点**避开道具图标的空白**（点中道具会弹浮层卡死流程）
        2. 必须确认"真的离开过鬼兵演武页"，才允许把 hero_fire 当作"回来了"
        3. 兜底的静止帧点击只在**新鲜帧**上触发（缓存帧的"静止"是假的）
        """
        start = time.time()
        left_gbb_page = False
        settle_logged = False
        battle_started = False
        still_frames = 0
        last_mean = None
        stale_frames = 0

        while time.time() - start < self.battle_timeout:
            self.check_stop()
            self.stuck_record_check()
            fresh = self.screenshot_fresh()

            if fresh:
                stale_frames = 0
            else:
                stale_frames += 1
                if stale_frames % 15 == 0:
                    log.warning("画面已连续 %d 帧无更新（PrintWindow 失败），继续尝试推进",
                                stale_frames)

            # 回到鬼兵演武页 = 整轮结束。只有新鲜帧能确认这件事。
            if fresh and self.is_gbb_page():
                if left_gbb_page:
                    log.info("已回到鬼兵演武页面（本轮 %.1fs）", time.time() - start)
                    return True
                log.warning("仍在鬼兵演武页，挑战未生效，本轮不算完成")
                return False

            if fresh:
                left_gbb_page = True

            # 结算界面推进：只认新鲜帧，只点避开道具的空白区
            if fresh:
                hit_win, sc_win, _ = self.appear("win")
                hit_de, sc_de, _ = self.appear("de_win")
                hit_re, sc_re, _ = self.appear("reward")
                hit_fail, _, _ = self.appear("battle_false")
                if hit_win or hit_de or hit_re or hit_fail:
                    if not settle_logged:
                        log.info("战斗结束（胜利=%.3f/%.3f 奖励=%.3f 失败=%s），"
                                 "用时 %.1fs，点空白推进结算",
                                 sc_win, sc_de, sc_re, hit_fail, time.time() - start)
                        settle_logged = True
                    self.click_settle_blank()
                    still_frames = 0
                    self.sleep(self.timing.get("after_close", (0.6, 1.0)))
                    continue

            # 统计静止帧（用于跳过冻结的胜利动画）
            mean = float(self.bgr.mean())
            if last_mean is not None and abs(mean - last_mean) < 2.0:
                still_frames += 1
            else:
                still_frames = 0
            last_mean = mean

            if not battle_started and time.time() - start > 8:
                battle_started = True

            # 兜底：应对游戏更新导致锚点模板失效。
            # 三个条件必须同时满足：新鲜帧 + 进战斗超 8s + 连续静止 ~2.4s。
            # 点哪里也要分情况：看得见结算锚点才敢点结算空白区，
            # 否则只点已知安全背景区（战斗中全屏乱点会把战斗卡死，御魂那边踩过）。
            if battle_started and still_frames >= 8 and fresh:
                if self.appear("win")[0] or self.appear("de_win")[0] or self.appear("reward")[0]:
                    log.info("画面静止且处于结算界面，点空白推进")
                    self.click_settle_blank(control_name="still_settle")
                else:
                    log.info("画面长时间静止且无已知按钮，安全空白区点击继续")
                    self.click_random_region(control_name="still_blank", repeat_check=False)
                still_frames = 0

            self.sleep(self.timing.get("poll", 0.3))

        log.warning("等待超过 %ds 仍未回到鬼兵演武页面", self.battle_timeout)
        return False
