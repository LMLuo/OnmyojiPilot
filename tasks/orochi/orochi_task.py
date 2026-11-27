"""
单人刷御魂 - 巫女大蛇·悲鸣（御魂11层）

起点：已停在"御魂副本 - 八岐大蛇 - 悲鸣层"界面（庭院→探索那跳由用户手动完成）
循环：点挑战 → 自动战斗 → 领奖 → 关闭宝藏 → 回到御魂界面 → 重复

设计对照 OAS：
- 战斗状态判断：多标志综合（OAS is_in_battle 用 I_BATTLE_INFO/I_WIN/I_FALSE/I_REWARD 任一）
- 领奖：连点直到 reward 消失（OAS battle_wait 的领奖循环）
- 防卡死：继承 BaseTask 的重复点击检测 + 超时检测
- 所有点击经 BaseTask.click 走 handle_control_check
"""
import os
import time
import random
import logging
import cv2

from core.base_task import (BaseTask, TaskEnd, ScriptStopped,
                            GameStuckError, GameTooManyClickError)
from core.window import WindowDevice
from core.matcher import Matcher

log = logging.getLogger("OnmyojiPilot")

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# 照搬 OAS：判定"在战斗中/战斗相关界面"的标志集合
BATTLE_FLAGS = ("reward", "win", "battle_false", "greed_ghost")


class OrochiTask(BaseTask):
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
        self.start_time = time.time()
        self.battle_timeout = config.get("battle_timeout", 180)
        # ---- 运行统计（供控制台界面读取）----
        self.stats = {
            "limit": self.limit,
            "done": 0,          # 成功完成次数
            "failed": 0,        # 失败轮数
            "retries": 0,       # 挑战点击重试次数
            "durations": [],    # 每轮耗时（秒）
            "state": "starting",
            "message": "",
        }
        self.started_at = time.time()
        self.finished_at = None

    def set_state(self, state, message=""):
        """更新状态，供界面轮询显示"""
        self.stats["state"] = state
        self.stats["message"] = message
        if state in ("finished", "stopped", "error"):
            self.finished_at = time.time()

    def run(self):
        log.info("=" * 55)
        log.info("单人刷御魂 - 巫女大蛇·悲鸣")
        log.info("目标 %d 次，点击方式: %s", self.limit, self.device.click_mode)
        log.info("=" * 55)

        try:
            if not self.wait_for_challenge_button(timeout=10):
                # 区分"投屏断了"和"界面没停在御魂页"，避免给出误导性报错
                if WindowDevice.is_idle_frame(self.image):
                    raise RuntimeError(
                        "ZenLink 投屏已断开（画面是 ZenLink 的\"已就绪/设备停止投屏\"界面）。\n"
                        "请先在 ZenLink 里点\"开始投屏\"恢复画面，再重新运行。"
                    )
                raise RuntimeError("未检测到挑战按钮，请确认已停在御魂副本-悲鸣层界面")
            log.info("已确认起点：御魂副本界面")
            self.set_state("running")

            while self.count < self.limit:
                self.check_stop()
                self.stuck_record_check()   # OAS 卡死检测
                log.info("----- 第 %d/%d 次 -----", self.count + 1, self.limit)

                if not self.battle_once():
                    self.stats["failed"] += 1
                    log.warning("本轮失败，尝试恢复界面")
                    self.close_result_screen()
                    if not self.wait_for_challenge_button(timeout=15):
                        log.error("无法回到御魂界面，任务中止")
                        self.set_state("error", "无法回到御魂界面，任务中止")
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

    # ---------- 界面判断（照搬 OAS 思路）----------
    def is_in_battle(self):
        """OAS is_in_battle：任一战斗标志出现即视为战斗相关界面"""
        for name in BATTLE_FLAGS:
            if self.appear(name)[0]:
                return True
        return False

    def is_in_soul_page(self):
        """是否在御魂副本选择界面（挑战按钮可见）"""
        return self.appear("fire_btn")[0]

    def wait_for_challenge_button(self, timeout=10):
        start = time.time()
        while time.time() - start < timeout:
            self.check_stop()
            if not self.screenshot_fresh():
                continue
            ok, score, pos = self.appear("fire_btn")
            if ok:
                return pos
            self.sleep(self.timing.get("wait_poll", 0.5))
        return None

    # ---------- 单轮战斗 ----------
    def battle_once(self):
        round_start = time.time()
        # 每轮开头清空点击历史（照搬 OAS battle_wait 开头的 click_record_clear）。
        # click_record 是跨轮累积的，不清的话「挑战」+ 结算空白的点击会攒起来，
        # 在若干轮之后误报"同一按钮点击过多"。
        self.click_record_clear()
        # 1. 点挑战 + 确认真的进战斗
        entered = False
        for attempt in range(1, 6):
            pos = self.wait_for_challenge_button(timeout=10)
            if not pos:
                # wait_for_challenge_button 在"一直是缓存帧"时也会返回 None，
                # 所以这里再确认一次挑战按钮到底还在不在
                ok, _sc, p = self.appear("fire_btn")
                if ok:
                    pos = p            # 按钮其实还在，继续点
                else:
                    # 挑战按钮不可见：多半已经在战斗/结算里，交给结算等待处理。
                    # 绝不在这里乱点（战斗中乱点可能关掉自动战斗）。
                    log.info("挑战按钮不可见（第 %d 次检查），转入结算等待", attempt)
                    break

            target = self.matcher.templates.get("fire_btn")
            x, y = target.click_point(pos, self.bgr) if target else pos
            log.info("点击挑战 @(%d,%d)（第 %d 次尝试）", x, y, attempt)
            self.click(x, y, control_name="fire_btn")
            if attempt > 1:
                self.stats["retries"] += 1

            # 2. 等挑战按钮消失 = 已进入战斗
            t_click = time.time()
            if self.wait_until_gone("fire_btn", timeout=8):
                log.info("已进入战斗（%.1fs）", time.time() - t_click)
                entered = True
                break

            # 超时后再看一眼：按钮还在 = 点击确实没生效，可以安全重试；
            # 按钮已不在 = 其实进去了，只是消失检测被拖到超时，别再点一次。
            if self.appear("fire_btn")[0]:
                log.warning("挑战按钮仍在，点击未生效，重试（已等 %.1fs）",
                            time.time() - t_click)
                continue
            log.info("挑战按钮已消失，判定已进入战斗（%.1fs）", time.time() - t_click)
            entered = True
            break

        if not entered and self.appear("fire_btn")[0]:
            # 关键：不能继续往下走。否则 wait_and_collect_reward 会因为
            # "还看得见挑战按钮"而误判为"已回到御魂界面"，把没打的算成打完了。
            log.error("点击挑战未生效（挑战按钮仍在），本轮作废")
            return False

        # 3. 等结算 + 领奖
        if not self.wait_and_collect_reward():
            log.warning("未检测到领奖图标")
            self.screenshot()
            if not self.appear("fire_btn")[0]:
                self.close_result_screen()
                self.count += 1
                self.stats["done"] = self.count
                self.stats["durations"].append(round(time.time() - round_start, 2))
                log.info("第 %d 次完成（未捕获领奖图标）", self.count)
                return True
            return False

        self.count += 1
        elapsed = time.time() - round_start
        self.stats["done"] = self.count
        self.stats["durations"].append(round(elapsed, 2))
        log.info("第 %d 次战斗完成，本轮耗时 %.1fs", self.count, elapsed)

        # 4. 关结算，回御魂界面
        self.close_result_screen()
        return True

    def wait_and_collect_reward(self):
        """
        等待战斗结束并推进结算。

        实测修正（重要）：结算界面**只有一屏**，不是两种。
        这一屏上同时存在：
            - 6 张掉落卡 + 魂小卡 + 金币 + 大红福袋  -> reward 锚点
            - 右下角"点击屏幕继续"                  -> click_continue 锚点
        早期版本误以为这是 A(魂翻牌)/B(掉落列表) 两种界面，
        A 分支直接点 reward 图标中心"领取"，结果点中道具弹出了
        "物品描述/获取途径"浮层：界面不前进、图标还在，
        于是被判定"还没点完"而反复点同一处，10 次后触发防卡死。
        实测日志：
            13:24:35 检测到魂翻牌 score=0.960，点击领取
            13:24:40 检测到魂翻牌 score=0.960，点击领取
            13:24:41 防卡死机制触发: 同一按钮点击过多: reward

        现在的做法照搬 OAS `reward_exclude_click`（battle_wait.py:1179）：
        **只在避开所有道具图标的空白处随机点**，靠点空白推进；
        就算真的弹出物品描述浮层，点空白也会把它关掉。
        退出条件仍是"回到御魂副本界面（挑战按钮可见）"。
        """
        start = time.time()
        last_mean = None
        still_frames = 0
        battle_started = False
        stale_frames = 0
        settle_logged = False
        # 必须先确认"真的离开过御魂页"，才允许把 fire_btn 当作"打完回到御魂页"。
        # 否则挑战没点进去时，第一帧就看得见 fire_btn，会被误判成一轮完成。
        left_soul_page = False

        while time.time() - start < self.battle_timeout:
            self.check_stop()
            self.stuck_record_check()
            fresh = self.screenshot_fresh()

            # PrintWindow 偶尔会长时间抓不到图（硬件渲染表面重建）。
            # 这时不能空转：缓存帧可以用来"推进"（点空白是低风险的），
            # 但不能用来判定"本轮完成"。这里只做可见性告警。
            if fresh:
                stale_frames = 0
            else:
                stale_frames += 1
                if stale_frames % 15 == 0:
                    log.warning("画面已连续 %d 帧无更新（PrintWindow 失败），继续尝试推进",
                                stale_frames)

            # 回到御魂界面 = 整轮结束。
            # 只有**新鲜帧**才能确认这件事；缓存帧上可能还留着打之前的御魂页，
            # 拿它判定会凭空多记一次完成。
            if fresh and self.appear("fire_btn")[0]:
                if left_soul_page:
                    return True
                log.warning("仍在御魂副本页，挑战未生效，本轮不算完成")
                return False
            if fresh:
                left_soul_page = True

            # 结算界面推进。只依赖新鲜帧（缓存帧会让锚点在旧画面上一直命中，
            # 导致对着早已消失的界面空点）。
            if fresh:
                hit_reward, score_r, _ = self.appear("reward")
                hit_cont, score_c, _ = self.appear("click_continue")
                if hit_reward or hit_cont:
                    if not settle_logged:
                        log.info("战斗结束（福袋=%.3f 继续=%.3f），用时 %.1fs，点空白推进结算",
                                 score_r, score_c, time.time() - start)
                        settle_logged = True
                    self.click_settle_blank()
                    still_frames = 0
                    self.sleep(self.timing.get("after_close", (0.6, 1.0)))
                    continue

            # 统计静止帧数（用于跳过冻结的胜利动画）
            mean = float(self.bgr.mean())
            if last_mean is not None and abs(mean - last_mean) < 2.0:
                still_frames += 1
            else:
                still_frames = 0
            last_mean = mean

            if not battle_started and time.time() - start > 8:
                battle_started = True

            # 兜底：应对游戏更新导致锚点模板失效。
            #
            # 三个必须同时满足的条件（都是实测踩出来的）：
            #   1. fresh —— 只认新鲜帧。窗口被最小化/PrintWindow 失败时拿到的是缓存帧，
            #      亮度当然和上一帧一样，"静止"是假的。之前没加这个判断，
            #      窗口一被最小化就会疯狂触发兜底点击。
            #   2. 进战斗超过 8s —— 避开战斗加载期。
            #   3. 连续静止 ~2.4s。
            #
            # 点哪里也很讲究：
            #   - 看得见结算锚点  -> 点结算空白区（全屏随机但避开道具图标）
            #   - 看不见锚点（多半在战斗中）-> 只点那 4 块已知安全背景区
            # 早期版本让兜底无条件全屏随机点，结果战斗中点到了界面按钮，
            # 把战斗卡住，第 3 轮整整空转了 30 多秒。
            if battle_started and still_frames >= 8 and fresh:
                if self.appear("reward")[0] or self.appear("click_continue")[0]:
                    log.info("画面静止且处于结算界面，点空白推进")
                    self.click_settle_blank(control_name="still_settle")
                else:
                    log.info("画面长时间静止且无已知按钮，安全空白区点击继续")
                    self.click_random_region(control_name="still_blank", repeat_check=False)
                still_frames = 0

            self.sleep(self.timing.get("poll", 0.3))
        return False

    def close_result_screen(self):
        """
        关闭"发现宝藏"结算界面，回到御魂副本界面。

        防机械点击改造（对照 OAS C_WIN + random.choice）：
        原来按固定顺序轮转 4 个固定坐标；现在从 close_regions 里**随机选**，
        且区域内正态随机取点、避免连续两次选同一块区域。
        """
        regions = list(self.close_regions.values())
        last = None
        for _ in range(25):
            self.check_stop()
            if not self.screenshot_fresh():
                continue
            if self.appear("fire_btn")[0]:
                log.info("已回到御魂副本界面")
                return True

            if regions:
                pool = [r for r in regions if r is not last] or regions
                target = random.choice(pool)
                last = target
                x, y = target.coord()
                name = f"close_result:{target.name}"
            else:
                x, y = 820, 380
                name = "close_result"
            # 点哪里：确认是结算界面就点"避开道具"的空白区，
            # 否则（可能是别的弹窗）退回旧的 close 区域，
            # 因为全屏随机点在未知界面上有风险（实测战斗中全屏乱点会把战斗卡死）。
            if self.appear("reward")[0] or self.appear("click_continue")[0]:
                self.click_settle_blank(control_name="close_result")
            else:
                self.click(x, y, control_name=name, repeat_check=False)
            self.sleep(self.timing.get("after_close", 0.8))
        log.warning("无法关闭结算界面")
        return False
