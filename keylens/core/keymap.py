"""键位映射与分类。

KeyLens 只记录"哪个物理键被按下"，因此本模块的职责是：
  * 把 Windows 虚拟键码 / 扫描码映射成稳定的键位标识（如 ``A``、``R_SHIFT``、``OEM_1``）；
  * 给每个键位一个人类可读显示名（如 ``A``、``RShift``、``; :``）；
  * 把键位归入类别（字母 / 数字 / 标点 / 编辑 / 功能 / 小键盘 / 修饰 / 其他），
    方便报表按类别汇总，而不是把内容拼回文本。

注意：本模块**不做**任何"键码 -> 字符"的还原。它只提供键位标签与分类，
所以数据库里存的东西无法被拼回你输入的文字。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# 扫描码（set 1）到键位标识。用于无扩展位时的 OEM 键与特殊键。
_SCAN_TO_KEY: Dict[int, str] = {
    0x01: "ESC",
    0x02: "1",
    0x03: "2",
    0x04: "3",
    0x05: "4",
    0x06: "5",
    0x07: "6",
    0x08: "7",
    0x09: "8",
    0x0A: "9",
    0x0B: "0",
    0x0C: "OEM_MINUS",
    0x0D: "OEM_PLUS",
    0x0E: "BACKSPACE",
    0x0F: "TAB",
    0x10: "Q",
    0x11: "W",
    0x12: "E",
    0x13: "R",
    0x14: "T",
    0x15: "Y",
    0x16: "U",
    0x17: "I",
    0x18: "O",
    0x19: "P",
    0x1A: "OEM_4",
    0x1B: "OEM_6",
    0x1C: "ENTER",
    0x1D: "L_CTRL",
    0x1E: "A",
    0x1F: "S",
    0x20: "D",
    0x21: "F",
    0x22: "G",
    0x23: "H",
    0x24: "J",
    0x25: "K",
    0x26: "L",
    0x27: "OEM_1",
    0x28: "OEM_7",
    0x29: "OEM_3",
    0x2A: "L_SHIFT",
    0x2B: "OEM_5",
    0x2C: "Z",
    0x2D: "X",
    0x2E: "C",
    0x2F: "V",
    0x30: "B",
    0x31: "N",
    0x32: "M",
    0x33: "OEM_COMMA",
    0x34: "OEM_PERIOD",
    0x35: "OEM_2",
    0x36: "R_SHIFT",
    0x37: "NUMPAD_MULTIPLY",
    0x38: "L_ALT",
    0x39: "SPACE",
    0x3A: "CAPSLOCK",
    0x3B: "F1",
    0x3C: "F2",
    0x3D: "F3",
    0x3E: "F4",
    0x3F: "F5",
    0x40: "F6",
    0x41: "F7",
    0x42: "F8",
    0x43: "F9",
    0x44: "F10",
    0x45: "NUMLOCK",
    0x46: "SCROLLLOCK",
    0x47: "NUMPAD7",
    0x48: "NUMPAD8",
    0x49: "NUMPAD9",
    0x4A: "NUMPAD_SUBTRACT",
    0x4B: "NUMPAD4",
    0x4C: "NUMPAD5",
    0x4D: "NUMPAD6",
    0x4E: "NUMPAD_ADD",
    0x4F: "NUMPAD1",
    0x50: "NUMPAD2",
    0x51: "NUMPAD3",
    0x52: "NUMPAD0",
    0x53: "NUMPAD_DECIMAL",
    0x56: "OEM_102",
    0x57: "F11",
    0x58: "F12",
}

# 扩展键（flags 带 0x01）扫描码到键位标识。
_EXT_SCAN_TO_KEY: Dict[int, str] = {
    0x1C: "NUMPAD_ENTER",
    0x1D: "R_CTRL",
    0x35: "NUMPAD_DIVIDE",
    0x37: "PRINTSCREEN",
    0x38: "R_ALT",
    0x45: "PAUSE",
    0x46: "BREAK",
    0x47: "HOME",
    0x48: "UP",
    0x49: "PAGEUP",
    0x4B: "LEFT",
    0x4D: "RIGHT",
    0x4F: "END",
    0x50: "DOWN",
    0x51: "PAGEDOWN",
    0x52: "INSERT",
    0x53: "DELETE",
    0x5B: "L_WIN",
    0x5C: "R_WIN",
    0x5D: "APPS",
}

# 虚拟键码兜底（个别键扫描码缺失时使用，例如部分笔记本的媒体键）。
_VK_TO_KEY: Dict[int, str] = {
    0x08: "BACKSPACE",
    0x09: "TAB",
    0x0D: "ENTER",
    0x10: "L_SHIFT",
    0x11: "L_CTRL",
    0x12: "L_ALT",
    0x13: "PAUSE",
    0x14: "CAPSLOCK",
    0x1B: "ESC",
    0x20: "SPACE",
    0x21: "PAGEUP",
    0x22: "PAGEDOWN",
    0x23: "END",
    0x24: "HOME",
    0x25: "LEFT",
    0x26: "UP",
    0x27: "RIGHT",
    0x28: "DOWN",
    0x2C: "PRINTSCREEN",
    0x2D: "INSERT",
    0x2E: "DELETE",
    0x5B: "L_WIN",
    0x5C: "R_WIN",
    0x5D: "APPS",
    0x5F: "SLEEP",
    0x90: "NUMLOCK",
    0x91: "SCROLLLOCK",
    0xA0: "L_SHIFT",
    0xA1: "R_SHIFT",
    0xA2: "L_CTRL",
    0xA3: "R_CTRL",
    0xA4: "L_ALT",
    0xA5: "R_ALT",
}

# 人类可读显示名：键位标识 -> 单行标签。
DISPLAY: Dict[str, str] = {
    "ESC": "Esc",
    "BACKSPACE": "Bksp",
    "TAB": "Tab",
    "ENTER": "Enter",
    "SPACE": "Space",
    "CAPSLOCK": "Caps",
    "L_SHIFT": "LShift",
    "R_SHIFT": "RShift",
    "L_CTRL": "LCtrl",
    "R_CTRL": "RCtrl",
    "L_ALT": "LAlt",
    "R_ALT": "RAlt",
    "L_WIN": "LWin",
    "R_WIN": "RWin",
    "APPS": "Menu",
    "OEM_1": "; :",
    "OEM_PLUS": "= +",
    "OEM_COMMA": ", <",
    "OEM_MINUS": "- _",
    "OEM_PERIOD": ". >",
    "OEM_2": "/ ?",
    "OEM_3": "` ~",
    "OEM_4": "[ {",
    "OEM_5": "\\ |",
    "OEM_6": "] }",
    "OEM_7": "' \"",
    "OEM_102": "< >",
    "PRINTSCREEN": "PrtSc",
    "SCROLLLOCK": "Scrlk",
    "NUMLOCK": "Num",
    "PAGEUP": "PgUp",
    "PAGEDOWN": "PgDn",
    "INSERT": "Ins",
    "DELETE": "Del",
    "HOME": "Home",
    "END": "End",
    "UP": "↑",
    "DOWN": "↓",
    "LEFT": "←",
    "RIGHT": "→",
    "SLEEP": "Sleep",
    "PAUSE": "Pause",
    "BREAK": "Break",
    # 小键盘：参考真实键盘刻印，运算键与回车只用符号，数字不加前缀
    "NUMPAD_ENTER": "Enter",
    "NUMPAD_DIVIDE": "/",
    "NUMPAD_MULTIPLY": "*",
    "NUMPAD_SUBTRACT": "-",
    "NUMPAD_ADD": "+",
    "NUMPAD_DECIMAL": ".",
    # 掩码键：命中隐私掩码时不记录具体按键，只记这一个桶。
    "MASKED": "(掩码)",
}

CATEGORIES: Tuple[str, ...] = (
    "letter",
    "digit",
    "punct",
    "edit",
    "func",
    "numpad",
    "modifier",
    "system",
    "masked",
    "other",
)

CATEGORY_DISPLAY: Dict[str, str] = {
    "letter": "字母",
    "digit": "数字",
    "punct": "标点",
    "edit": "编辑",
    "func": "功能键",
    "numpad": "小键盘",
    "modifier": "修饰键",
    "system": "系统键",
    "masked": "掩码（未记录具体键）",
    "other": "其他",
}

_DIGITS = set("0123456789")
_EDIT = {
    "BACKSPACE", "ENTER", "TAB", "SPACE", "DELETE", "INSERT", "ESC",
    # 光标移动与翻页：与编辑行为强相关，归入"编辑"而不是"其他"
    "HOME", "END", "PAGEUP", "PAGEDOWN", "UP", "DOWN", "LEFT", "RIGHT",
}
_MODIFIER = {
    "L_SHIFT",
    "R_SHIFT",
    "L_CTRL",
    "R_CTRL",
    "L_ALT",
    "R_ALT",
    "L_WIN",
    "R_WIN",
    "CAPSLOCK",
    "NUMLOCK",
    "SCROLLLOCK",
}
_SYSTEM = {"APPS", "PRINTSCREEN", "PAUSE", "BREAK", "SLEEP"}
_PUNCT_PREFIX = ("OEM_",)


def resolve(vk: int, scan: int, extended: bool) -> str:
    """把一次按键解析成稳定的键位标识。

    解析顺序：扩展扫描码 -> 普通扫描码 -> 虚拟键码兜底 -> ``VK_xx``。
    """
    if extended and scan in _EXT_SCAN_TO_KEY:
        return _EXT_SCAN_TO_KEY[scan]
    if not extended and scan in _SCAN_TO_KEY:
        return _SCAN_TO_KEY[scan]
    if scan in _EXT_SCAN_TO_KEY and scan in (0x1C, 0x1D, 0x35):
        # 少数键盘不带扩展位地发送小键盘回车 / 右 Ctrl / 小键盘除号。
        return _EXT_SCAN_TO_KEY[scan]
    if vk in _VK_TO_KEY:
        return _VK_TO_KEY[vk]
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return "NUMPAD%d" % (vk - 0x60)
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    return "VK_%02X" % vk


def category(key: str) -> str:
    """给键位标识归类，用于按类别汇总。"""
    if key == "MASKED":
        return "masked"
    if len(key) == 1 and key.isalpha():
        return "letter"
    if key in _DIGITS:
        return "digit"
    if key.startswith("NUMPAD"):
        return "numpad"
    if key in _MODIFIER:
        return "modifier"
    if key in _EDIT:
        return "edit"
    if key.startswith("F") and key[1:].isdigit():
        return "func"
    if key.startswith(_PUNCT_PREFIX):
        return "punct"
    if key in _SYSTEM:
        return "system"
    return "other"


def display(key: str) -> str:
    """键位标识 -> 界面显示名。"""
    if key in DISPLAY:
        return DISPLAY[key]
    if key.startswith("NUMPAD"):
        # 小键盘数字带"小键盘"前缀，避免与主键区数字混淆
        tail = key[6:]
        digits = {
            "0": "0", "1": "1", "2": "2", "3": "3", "4": "4",
            "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
        }
        if tail in digits:
            return "小键盘" + tail
        return "小键盘" + tail.replace("_", " ")
    return key


def display_compact(key: str) -> str:
    """紧凑显示名：键框实在放不下全名时用。

    窗口很小的时候键宽只有二十来像素，"小键盘7" 缩到最小字号还是顶出框线，
    这时只留数字（数字键区本来就有单独的标题，不会认错）。
    """
    if key.startswith("NUMPAD") and len(key) == 7 and key[6].isdigit():
        return key[6]
    return display(key)


# 主键盘区布局：每行是一个 (键位标识, 相对宽度) 列表，宽度以 1 个字母键为基准。
# 布局只用于画热力图，不含任何内容信息。
KEYBOARD_ROWS: List[List[Tuple[str, float]]] = [
    [("OEM_3", 1), ("1", 1), ("2", 1), ("3", 1), ("4", 1), ("5", 1), ("6", 1),
     ("7", 1), ("8", 1), ("9", 1), ("0", 1), ("OEM_MINUS", 1), ("OEM_PLUS", 1),
     ("BACKSPACE", 2)],
    [("TAB", 1.5), ("Q", 1), ("W", 1), ("E", 1), ("R", 1), ("T", 1), ("Y", 1),
     ("U", 1), ("I", 1), ("O", 1), ("P", 1), ("OEM_4", 1), ("OEM_6", 1),
     ("OEM_5", 1.5)],
    [("CAPSLOCK", 1.75), ("A", 1), ("S", 1), ("D", 1), ("F", 1), ("G", 1),
     ("H", 1), ("J", 1), ("K", 1), ("L", 1), ("OEM_1", 1), ("OEM_7", 1),
     ("ENTER", 2.25)],
    [("L_SHIFT", 2.25), ("Z", 1), ("X", 1), ("C", 1), ("V", 1), ("B", 1),
     ("N", 1), ("M", 1), ("OEM_COMMA", 1), ("OEM_PERIOD", 1), ("OEM_2", 1),
     ("R_SHIFT", 2.75)],
    [("L_CTRL", 1.25), ("L_WIN", 1.25), ("L_ALT", 1.25), ("SPACE", 6.25),
     ("R_ALT", 1.25), ("R_WIN", 1.25), ("APPS", 1.25), ("R_CTRL", 1.25)],
]

# 小键盘：+ 与 Enter 纵向跨 2 行、0 横向跨 2 格，None 表示被跨行键占掉的空位。
# 三元组 (键名, 宽, 高) 表示纵向跨行的键。
NUMPAD_ROWS: List[List[Optional[Tuple[str, float]]]] = [
    [("NUMLOCK", 1), ("NUMPAD_DIVIDE", 1), ("NUMPAD_MULTIPLY", 1), ("NUMPAD_SUBTRACT", 1)],
    [("NUMPAD7", 1), ("NUMPAD8", 1), ("NUMPAD9", 1), ("NUMPAD_ADD", 1, 2)],
    [("NUMPAD4", 1), ("NUMPAD5", 1), ("NUMPAD6", 1), None],
    [("NUMPAD1", 1), ("NUMPAD2", 1), ("NUMPAD3", 1), ("NUMPAD_ENTER", 1, 2)],
    [("NUMPAD0", 2), ("NUMPAD_DECIMAL", 1), None],
]

# 控制键区（导航簇）：顶部一行 PrtSc / Scroll / Pause 悬在编辑三键正上方，
# 编辑三键之下留一空行，倒 T 方向键的底边与小键盘 "0 ." 行、主键盘底行对齐。
NAV_ROWS: List[List[Optional[Tuple[str, float]]]] = [
    [("PRINTSCREEN", 1), ("SCROLLLOCK", 1), ("PAUSE", 1)],
    [("INSERT", 1), ("HOME", 1), ("PAGEUP", 1)],
    [("DELETE", 1), ("END", 1), ("PAGEDOWN", 1)],
    [None, None, None],
    [None, ("UP", 1), None],
    [("LEFT", 1), ("DOWN", 1), ("RIGHT", 1)],
]

# 兼容旧名字
EDIT_ROWS = NAV_ROWS

# 功能键行：Esc 与 F1~F4 / F5~F8 / F9~F12 分组，组间留空。
# 空隙用浮点数表示（单位宽度）：Esc 后 1u、两处分组之间各 0.5u，
# 三段空隙合计 2u，整行正好 15u——F12 右缘与主键盘最宽行对齐。
# PrtSc/Scroll/Pause 归入控制键区列。
FUNCTION_ROW: List[object] = [
    ("ESC", 1), 1.0,
    ("F1", 1), ("F2", 1), ("F3", 1), ("F4", 1), 0.5,
    ("F5", 1), ("F6", 1), ("F7", 1), ("F8", 1), 0.5,
    ("F9", 1), ("F10", 1), ("F11", 1), ("F12", 1),
]

# 画热力图时要跳过的键：它们要么每次都参与、要么没有位置语义。
HEATMAP_SKIP = {"MASKED"}


def heatmap_keys() -> List[str]:
    """返回热力图需要展示的全部键位标识（按布局顺序）。"""
    keys: List[str] = []
    all_rows: List[List[Optional[Tuple[str, float]]]] = [FUNCTION_ROW]
    for rows in (KEYBOARD_ROWS, NUMPAD_ROWS, NAV_ROWS):
        all_rows.extend(rows)
    for row in all_rows:
        for item in row:
            if isinstance(item, tuple):
                keys.append(item[0])
    return [k for k in keys if k not in HEATMAP_SKIP]


def layout_blocks() -> List[Tuple[str, List[List[Optional[Tuple[str, float]]]]]]:
    """给绘图和 HTML 报表共用的分块布局。"""
    return [
        ("功能键区", [FUNCTION_ROW]),
        ("主键盘区", KEYBOARD_ROWS),
        ("数字键区", NUMPAD_ROWS),
        ("控制键区", NAV_ROWS),
    ]


# 物理键位总数，用于估算"键盘覆盖率"（有多少键你从来没用过）。
TOTAL_TRACKED_KEYS = len(heatmap_keys())

_KEY_INDEX: Optional[Dict[str, int]] = None


def key_index() -> Dict[str, int]:
    """键位标识 -> 序号，供绘图时定位使用。"""
    global _KEY_INDEX
    if _KEY_INDEX is None:
        _KEY_INDEX = {k: i for i, k in enumerate(heatmap_keys())}
    return _KEY_INDEX


def is_supported(key: str) -> bool:
    """判断键位标识是否属于已知布局（未知键仍会被统计，只是不上热力图）。"""
    return key in key_index()
