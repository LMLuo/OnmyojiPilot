"""
随机工具 —— 精确移植 OAS `module/base/utils/utils.py`

OAS 防机械点击的根基：**用"多次均匀随机取平均"模拟正态分布**，
而不是 `random.uniform` 的直接均匀分布。
均匀分布会让点击在矩形边界上均匀铺开，长按/滑动容易露馅；
正态分布让点击自然聚在中心、偶尔偏一点，更像真人。

同一套函数同时用于两件事，这很关键：
- 坐标：RuleClick.coord()
- 时长：ensure_time((0.1, 0.3))，所以连"点多久、等多久"都有自然波动
"""
import random

import numpy as np


def random_normal_distribution_int(a, b, n=3):
    """
    在 [a, b) 内产生正态分布的随机整数。

    照搬 OAS：取 n 个均匀随机数求均值，中心极限定理天然逼近正态分布。
    n 越大越集中在中心（n=3 是 OAS 默认值，波动幅度刚好）。

    Args:
        a (int): 区间下界（含）
        b (int): 区间上界（不含）
        n (int): 参与平均的随机数个数

    Returns:
        int
    """
    if a < b:
        return int(np.mean(np.random.randint(a, b, size=n)).round())
    return b


def random_rectangle_point(area, n=3):
    """
    在矩形区域内按正态分布取一点。

    Args:
        area: (x, y, w, h) —— 注意本项目统一用 x/y/w/h，
              与 OAS 的 (left, top, right, bottom) 口径不同。
        n (int): 模拟正态分布的取样数

    Returns:
        tuple(int): (x, y)
    """
    x, y, w, h = area
    return (random_normal_distribution_int(x, x + w, n=n),
            random_normal_distribution_int(y, y + h, n=n))


def ensure_time(second, n=3, precision=3):
    """
    把"时长"统一成一个具体数值。

    照搬 OAS：支持传 (下限, 上限) 元组，返回**正态分布**的随机时长。
    这样 `sleep((0.2, 0.5))` 每次等的时间都不一样，避免固定节拍。

    Args:
        second: int / float / tuple / str（"0.1-0.3" 或 "0.1,0.3"）
        n (int): 模拟正态分布的取样数
        precision (int): 小数位

    Returns:
        float
    """
    if isinstance(second, tuple):
        multiply = 10 ** precision
        result = random_normal_distribution_int(
            int(second[0] * multiply), int(second[1] * multiply), n) / multiply
        return round(result, precision)

    if isinstance(second, str):
        if ',' in second or '-' in second:
            sep = ',' if ',' in second else '-'
            lower, upper = second.replace(' ', '').split(sep)
            return ensure_time((float(lower), float(upper)), n=n, precision=precision)
        return float(second)

    return second


def ensure_int(*args):
    """把坐标统一转成 int（OAS 同名函数）"""
    result = []
    for arg in args:
        if isinstance(arg, (tuple, list)):
            result.append(tuple(int(round(x)) for x in arg))
        else:
            result.append(int(round(arg)))
    return result[0] if len(result) == 1 else tuple(result)


__all__ = [
    "random_normal_distribution_int",
    "random_rectangle_point",
    "ensure_time",
    "ensure_int",
    "random",
]
