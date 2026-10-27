"""热力配色：界面画布与静态预览共用，保证"预览"和"实际"永远一个样。"""

from __future__ import annotations

from typing import Tuple

from . import theme as palette


def lerp(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> str:
    """两个 RGB 之间线性插值，返回 ``#rrggbb``。"""
    t = max(0.0, min(1.0, t))
    r = int(round(c1[0] + (c2[0] - c1[0]) * t))
    g = int(round(c1[1] + (c2[1] - c1[1]) * t))
    b = int(round(c1[2] + (c2[2] - c1[2]) * t))
    return "#%02x%02x%02x" % (r, g, b)


def heat_color(value: int, maximum: int) -> str:
    """按次数给键盘格子上色。0 次为冷灰，越高越暖。

    用 0.55 次幂压一下长尾，否则几个高频键会把其余键全压成背景色。
    """
    if value <= 0:
        return palette.KEY_IDLE
    ratio = (value / maximum) ** 0.55 if maximum > 0 else 0.0
    if ratio < 0.5:
        return lerp(palette.HEAT_LOW, palette.HEAT_MID, ratio / 0.5)
    return lerp(palette.HEAT_MID, palette.HEAT_HIGH, (ratio - 0.5) / 0.5)


__all__ = ["lerp", "heat_color"]
