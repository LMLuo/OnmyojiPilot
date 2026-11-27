"""
防卡死机制自测（不连设备，纯内存跑）

为什么单独写这个
----------------
`click_record` 的计数规则很容易改出"看着没事、跑几轮才炸"的 bug。
真实症状是：传承试炼跑到第 5 轮报「同一按钮点击过多: settle」。
根因是结算空白点击（repeat_check=False）**只跳过检查、但仍然记录**，
数量攒够了在下一轮点「挑战」时才一起结算。

这种 bug 靠"跑一遍看看"发现成本很高（一轮一分钟），
所以把判定规则本身做成可重复跑的断言。

用法：
    python tools/selftest_antistuck.py
全部通过返回 0，有失败返回 1。
"""
import os
import sys

BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT_ROOT)

from core.base_task import BaseTask, GameTooManyClickError   # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        FAIL.append("%s -> %s" % (name, e))
        print("  [失败] %s\n         %s" % (name, e))
    except Exception as e:                      # noqa: BLE001
        FAIL.append("%s -> 意外异常 %r" % (name, e))
        print("  [失败] %s\n         意外异常 %r" % (name, e))
    else:
        PASS.append(name)
        print("  [通过] %s" % name)


class FakeDevice:
    """只提供 click，别的都不需要"""

    click_mode = "selftest"
    frame_fresh = True

    def __init__(self):
        self.clicks = []

    def click(self, x, y, duration=0.08):
        self.clicks.append((x, y))


def new_task():
    return BaseTask(FakeDevice(), None,
                    timing={"click_dur": (0.01, 0.02), "poll": (0.01, 0.02)})


# ---------------------------------------------------------------- 用例
def case_blank_click_not_recorded():
    """空白点击（repeat_check=False）不应写进 click_record"""
    t = new_task()
    for i in range(20):
        t.click(100 + i, 200, control_name="settle", repeat_check=False)
    assert len(t.click_record) == 0, \
        "click_record 里还有 %d 条，说明空白点击被记录了" % len(t.click_record)


def case_blank_then_checked_no_raise():
    """先点 20 次结算空白，再点一次带检查的按钮：不能报错"""
    t = new_task()
    for i in range(20):
        t.click(100 + i, 200, control_name="settle", repeat_check=False)
    try:
        t.click(763, 343, control_name="hero_fire")
    except GameTooManyClickError as e:
        raise AssertionError("误报防卡死：%s（这就是第 5 轮炸掉的那个 bug）" % e)


def case_same_button_still_trips():
    """同一个按钮连点 10 次，防卡死必须还能触发"""
    t = new_task()
    raised = False
    try:
        for _ in range(BaseTask.CLICK_RECORD_SAME_MAX):
            t.click(763, 343, control_name="hero_fire")
    except GameTooManyClickError:
        raised = True
    assert raised, "连点 10 次居然没触发防卡死，保护失效了"


def case_two_buttons_trips():
    """两个按钮各 6 次，6+6 规则必须生效"""
    t = new_task()
    raised = False
    try:
        for _ in range(6):
            t.click(763, 343, control_name="hero_fire")
            t.click(502, 245, control_name="confirm_start")
    except GameTooManyClickError:
        raised = True
    assert raised, "两个按钮各点 6 次没触发防卡死"


def case_clear_resets():
    """click_record_clear 之后计数归零，能再点满一轮"""
    t = new_task()
    for _ in range(9):
        t.click(763, 343, control_name="hero_fire")
    t.click_record_clear()
    assert len(t.click_record) == 0
    t.click(763, 343, control_name="hero_fire")   # 不该炸


def case_round_boundary_survives_many_rounds():
    """
    模拟 20 轮的点击构成：每轮「挑战」1 次 + 结算空白 4 次 + 点空白推进 2 次，
    每轮开头 click_record_clear()。这是真实流程的缩影，必须全程不报错。
    """
    t = new_task()
    for rnd in range(1, 21):
        t.click_record_clear()                       # battle_once 开头那句
        t.click(763, 343, control_name="hero_fire")
        for i in range(4):
            t.click(300 + i, 300, control_name="settle", repeat_check=False)
        for i in range(2):
            t.click(400 + i, 120, control_name="still_blank", repeat_check=False)


def main():
    print("防卡死机制自测\n" + "-" * 52)
    check("空白点击不写入 click_record", case_blank_click_not_recorded)
    check("20 次空白点击 + 1 次按钮点击不误报", case_blank_then_checked_no_raise)
    check("同按钮 10 次仍然触发防卡死", case_same_button_still_trips)
    check("两按钮各 6 次仍然触发防卡死", case_two_buttons_trips)
    check("click_record_clear 能归零", case_clear_resets)
    check("连跑 20 轮不误报", case_round_boundary_survives_many_rounds)

    print("-" * 52)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("\n失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
