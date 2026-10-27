"""KeyLens 配色常量（暗色 / 明亮双主题，运行时可切换）。

层次从深到浅：BG（窗底）→ BG_PANEL（卡片）→ BG_SOFT（输入/按钮），
描边 BORDER 只比卡片亮一档，形成克制的分区线。

set_mode() 直接重写模块级常量，界面代码照常读 palette.X；
切换主题时配合 snapshot() 构造旧值→新值映射，把已有控件换色。
"""

_DARK = {
    "BG": "#0f131a",        # 窗口底色
    "BG_PANEL": "#171d27",  # 卡片底
    "BG_SOFT": "#1f2734",   # 输入框 / 按钮底
    "BORDER": "#29344a",    # 卡片描边（仅比卡片亮一档）
    "TEXT": "#dae2ee",
    "TEXT_DIM": "#8d99ac",
    "TEXT_BRIGHT": "#f5f8fd",
    "ACCENT": "#4d9fff",
    "ACCENT_DIM": "#2f6db8",
    "OK": "#41d495",
    "WARN_BG": "#4a3208",
    "WARN_TEXT": "#ffcf70",
    "ALERT": "#ff6b6b",
    "KEY_IDLE": "#273040",
    "KEY_BORDER": "#3b485d",
    "HEAT_LOW": (46, 88, 140),
    "HEAT_MID": (58, 150, 190),
    "HEAT_HIGH": (255, 138, 76),
}

_LIGHT = {
    "BG": "#eef1f6",
    "BG_PANEL": "#ffffff",
    "BG_SOFT": "#e3e9f1",
    "BORDER": "#c4cede",
    "TEXT": "#2b3542",
    "TEXT_DIM": "#697482",
    "TEXT_BRIGHT": "#101826",
    "ACCENT": "#2e7fd9",
    "ACCENT_DIM": "#1c5c9e",
    "OK": "#1d9e68",
    "WARN_BG": "#fff2cf",
    "WARN_TEXT": "#8a5c00",
    "ALERT": "#d64949",
    "KEY_IDLE": "#dfe6ef",
    "KEY_BORDER": "#b7c4d4",
    "HEAT_LOW": (168, 199, 235),
    "HEAT_MID": (58, 150, 190),
    "HEAT_HIGH": (240, 120, 50),
}

_MODES = {"dark": _DARK, "light": _LIGHT}
_mode = "dark"

# 模块级常量声明：静态可见（类型检查可解析），运行时由 set_mode 重写为所选主题
BG: str = _DARK["BG"]
BG_PANEL: str = _DARK["BG_PANEL"]
BG_SOFT: str = _DARK["BG_SOFT"]
BORDER: str = _DARK["BORDER"]
TEXT: str = _DARK["TEXT"]
TEXT_DIM: str = _DARK["TEXT_DIM"]
TEXT_BRIGHT: str = _DARK["TEXT_BRIGHT"]
ACCENT: str = _DARK["ACCENT"]
ACCENT_DIM: str = _DARK["ACCENT_DIM"]
OK: str = _DARK["OK"]
WARN_BG: str = _DARK["WARN_BG"]
WARN_TEXT: str = _DARK["WARN_TEXT"]
ALERT: str = _DARK["ALERT"]
KEY_IDLE: str = _DARK["KEY_IDLE"]
KEY_BORDER: str = _DARK["KEY_BORDER"]
HEAT_LOW: tuple[int, int, int] = _DARK["HEAT_LOW"]
HEAT_MID: tuple[int, int, int] = _DARK["HEAT_MID"]
HEAT_HIGH: tuple[int, int, int] = _DARK["HEAT_HIGH"]


def set_mode(mode: str) -> None:
    """切换主题（"dark" / "light"），重写模块级颜色常量；未知值回落暗色。"""
    global _mode
    _mode = mode if mode in _MODES else "dark"
    for name, value in _MODES[_mode].items():
        globals()[name] = value


def get_mode() -> str:
    """当前主题名："dark" 或 "light"。"""
    return _mode


def snapshot() -> dict:
    """当前全部颜色常量的副本（切换前后各取一份可构造换色映射）。"""
    return {name: globals()[name] for name in _DARK}


__all__ = list(_DARK) + ["set_mode", "get_mode", "snapshot"]
