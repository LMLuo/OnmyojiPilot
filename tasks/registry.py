"""
任务注册表 —— 以后新增副本模块，只要在这里加一条。

设计目标
--------
网页控制台的"选任务"完全由这里的 TASKS 驱动：
加一条 spec，前端自动多出一张任务卡，不用改 server.py，也不用改前端代码。

新增一个任务需要准备
--------------------
1. 一个任务类（继承 core.base_task.BaseTask，实现 run()），
   放在 tasks/<模块名>/ 下，参考 tasks/orochi/orochi_task.py；
2. 一份模板配置和模板目录（结构同 config.TEMPLATES）；
3. 交互区域（click_regions / close_regions / settle_exclude）、时长表（timing）；
4. 在下面 TASKS 里追加一条 TaskSpec。

还没实现的任务先登记成 available=False，前端会显示成"规划中"的灰卡，
这样一眼能看出系统支持扩展，也不会误点。
"""
import os
from dataclasses import dataclass, field

from config import (TEMPLATES, CLICK_REGIONS, CLOSE_REGIONS,
                    SETTLE_EXCLUDE, TIMING, OROCHI_CONFIG)
from tasks.orochi.orochi_task import OrochiTask

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class TaskSpec:
    """一个可挂机模块的完整描述"""

    # ---- 给界面看 ----
    id: str                 # 唯一标识，接口里用它
    name: str               # 主标题，如"巫女大蛇 · 悲鸣"
    group: str              # 归类，如"御魂"
    subtitle: str           # 一句话说明
    desc: str               # 更长的说明（显示在卡片里）
    icon: str               # 像素图标名（前端 PIXEL_ART 的 key）
    accent: str             # 卡片强调色（十六进制）
    default_limit: int = 30
    available: bool = True

    # ---- 给运行器用 ----
    cls: object = None                      # 任务类，未实现时 None
    template_dir: str = ""                  # 模板目录
    templates: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    click_regions: dict = field(default_factory=dict)
    close_regions: dict = field(default_factory=dict)
    settle_exclude: list = field(default_factory=list)
    timing: dict = field(default_factory=dict)


OROCHI_TEMPLATE_DIR = os.path.join(BASE_DIR, "tasks", "orochi", "templates")

TASKS = [
    TaskSpec(
        id="orochi",
        name="巫女大蛇 · 悲鸣",
        group="御魂",
        subtitle="单人刷御魂十一层",
        desc="从御魂副本界面开始，自动挑战并领取结算奖励，刷满指定次数后停止。",
        icon="daruma",
        accent="#c8402c",
        default_limit=30,
        available=True,
        cls=OrochiTask,
        template_dir=OROCHI_TEMPLATE_DIR,
        templates=TEMPLATES,
        config=OROCHI_CONFIG,
        click_regions=CLICK_REGIONS,
        close_regions=CLOSE_REGIONS,
        settle_exclude=SETTLE_EXCLUDE,
        timing=TIMING,
    ),
    # ---- 以下为规划中：结构已就位，只差各自的识别资产与流程 ----
    TaskSpec(
        id="awake",
        name="觉醒副本",
        group="觉醒",
        subtitle="刷觉醒材料",
        desc="按属性选择觉醒层数，自动循环挑战。",
        icon="torii",
        accent="#7a5bd6",
        default_limit=30,
        available=False,
    ),
    TaskSpec(
        id="soul",
        name="御灵",
        group="御灵",
        subtitle="刷御灵副本",
        desc="御灵讨伐，需要单独的关卡与出战配置。",
        icon="magatama",
        accent="#2f8f6f",
        default_limit=30,
        available=False,
    ),
    TaskSpec(
        id="explore",
        name="探索副本",
        group="探索",
        subtitle="章节刷狗粮",
        desc="按章节循环探索，自动处理小怪与首领。",
        icon="scroll",
        accent="#b8792b",
        default_limit=20,
        available=False,
    ),
]

TASK_MAP = {t.id: t for t in TASKS}
DEFAULT_TASK_ID = next(t.id for t in TASKS if t.available)


def get_task(task_id=None):
    """取任务 spec；task_id 为空或不存在时回退到默认任务"""
    if not task_id:
        return TASK_MAP[DEFAULT_TASK_ID]
    return TASK_MAP.get(task_id) or TASK_MAP[DEFAULT_TASK_ID]


def public_tasks():
    """给前端的任务列表（不含类对象等内部字段）"""
    return [
        {
            "id": t.id,
            "name": t.name,
            "group": t.group,
            "subtitle": t.subtitle,
            "desc": t.desc,
            "icon": t.icon,
            "accent": t.accent,
            "default_limit": t.default_limit,
            "available": t.available,
        }
        for t in TASKS
    ]
