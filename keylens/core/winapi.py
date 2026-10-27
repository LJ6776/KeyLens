"""Win32 底层封装：全局键盘钩子、前台窗口、空闲状态、远程会话探测。

只用 ctypes 调系统 DLL，不依赖 pywin32 / pynput。键盘钩子运行在独立线程里，
回调函数必须在 1 毫秒级返回，所以这里只做"解析 + 入队"，一律不做 IO。

关于远程输入：向日葵 / ToDesk / TeamViewer 这类工具是把键盘事件注入到本机
输入队列的，因此经过远程会话打字时，本模块的低级钩子**同样会收到事件**，
事件结构与本地按键一致（注入事件带 LLKHF_INJECTED 标志，本模块照常记录）。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import keymap

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- 常量

WH_KEYBOARD_LL = 13

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_LOWER_IL_INJECTED = 0x02

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

KEY_DOWN_MESSAGES = (WM_KEYDOWN, WM_SYSKEYDOWN)

# 远程控制类软件：进程名（小写，不含扩展名）与窗口标题关键字。
REMOTE_PROCESS_NAMES = {
    # 国内常见
    "sunloginclient", "sunlogin", "orayremote", "向日葵", "todesk", "aweray_remote",
    # 通用远控
    "teamviewer", "anydesk", "rustdesk", "mstsc", "rdpclip", "parsec", "nomachine",
    "nxplayer", "nxserver", "splashtop", "radmin", "screenconnect", "ammyy",
    "dwagent", "getscreen", "zohoremoteserver", "logmein", "bomgar", "btremote",
    "jumpdesktop", "remotedesktopmanager", "remotedesktop", "chromeremotedesktop",
    # VNC 系（解压软件不会进这个名单，别混淆）
    "vncviewer", "winvnc", "tightvnc", "tvnserver", "ultravnc", "uvnc", "realvnc",
    "vncserver", "x11vnc",
}

# 进程名（去扩展名、小写）-> 友好显示名，给状态栏 / 浮层 / 报告用
REMOTE_APP_LABELS = {
    "sunloginclient": "向日葵", "sunlogin": "向日葵", "向日葵": "向日葵",
    "orayremote": "向日葵", "todesk": "ToDesk", "aweray_remote": "ToDesk",
    "teamviewer": "TeamViewer", "anydesk": "AnyDesk", "rustdesk": "RustDesk",
    "mstsc": "远程桌面(RDP)", "rdpclip": "远程桌面(RDP)",
    "remotedesktop": "Chrome 远程桌面", "chromeremotedesktop": "Chrome 远程桌面",
    "vncviewer": "VNC", "winvnc": "VNC", "tightvnc": "VNC", "tvnserver": "VNC",
    "ultravnc": "VNC", "uvnc": "VNC", "realvnc": "VNC", "vncserver": "VNC", "x11vnc": "VNC",
    "parsec": "Parsec", "nomachine": "NoMachine", "nxplayer": "NoMachine",
    "nxserver": "NoMachine", "splashtop": "Splashtop", "radmin": "Radmin",
    "screenconnect": "ScreenConnect", "ammyy": "Ammyy", "dwagent": "DwService",
    "getscreen": "GetScreen", "zohoremoteserver": "Zoho Assist", "logmein": "LogMeIn",
    "bomgar": "Bomgar", "btremote": "BeyondTrust", "jumpdesktop": "Jump Desktop",
    "remotedesktopmanager": "Remote Desktop Manager",
}

REMOTE_TITLE_KEYWORDS = (
    "向日葵",
    "sunlogin",
    "todesk",
    "teamviewer",
    "anydesk",
    "rustdesk",
    "远程桌面",
    "远程控制",
    "remote desktop",
    "remote support",
    "vnc",
    "radmin",
    "splashtop",
    "parsec",
    "assist",
)

# ---------------------------------------------------------------- 结构体


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

# ---------------------------------------------------------------- 函数签名

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t

user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int

user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL

user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

if hasattr(user32, "GetWindowLongPtrW"):
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
else:  # 32 位 Windows 上只有不带 Ptr 的版本
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long

kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetTickCount.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL


# ---------------------------------------------------------------- 数据类


@dataclass(frozen=True)
class KeyEvent:
    """一次物理按键。只含键位标识与来源信息，不含文本内容。"""

    ts: float
    key: str
    vk: int
    scan: int
    extended: bool
    injected: bool

    @property
    def category(self) -> str:
        return keymap.category(self.key)


@dataclass(frozen=True)
class WindowInfo:
    """前台窗口归属，用于把按键统计归到某个应用名下。"""

    hwnd: int
    pid: int
    process: str  # 形如 chrome.exe，取不到时为空串
    title: str  # 窗口标题（可能含文档名，可被配置项屏蔽）


# ---------------------------------------------------------------- 键盘钩子


class KeyboardHook:
    """全局低级键盘钩子（WH_KEYBOARD_LL）。

    钩子挂在独立线程上，该线程跑标准 Win32 消息循环；
    回调里只解析数据并投递给 ``on_event``，绝不阻塞，避免拖慢系统输入。
    """

    def __init__(self, on_event: Callable[[KeyEvent], None], on_error: Optional[Callable[[str], None]] = None):
        self._on_event = on_event
        self._on_error = on_error
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._hook = None
        self._ready = threading.Event()
        self._running = False
        # 保存 HOOKPROC 引用，防止被 GC 回收导致回调崩溃。
        self._proc = HOOKPROC(self._hook_proc)
        self._error: Optional[str] = None

    # -- 生命周期 -------------------------------------------------

    @property
    def error(self) -> Optional[str]:
        return self._error

    def start(self, timeout: float = 5.0) -> bool:
        if self._running:
            return True
        self._thread = threading.Thread(target=self._run, name="keylens-hook", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._running

    def stop(self, timeout: float = 3.0) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        self._hook = None
        self._thread = None
        self._thread_id = None
        self._ready.clear()

    # -- 内部实现 -------------------------------------------------

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            self._error = "SetWindowsHookExW 失败，错误码 %d" % ctypes.get_last_error()
            if self._on_error:
                self._on_error(self._error)
            self._ready.set()
            return
        self._running = True
        self._ready.set()
        msg = MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._running = False

    def _hook_proc(self, n_code, w_param, l_param):  # noqa: N802 (Win32 命名)
        if n_code == 0 and self._running:
            try:
                if int(w_param) in KEY_DOWN_MESSAGES:
                    info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = int(info.vkCode)
                    scan = int(info.scanCode)
                    flags = int(info.flags)
                    extended = bool(flags & LLKHF_EXTENDED)
                    key = keymap.resolve(vk, scan, extended)
                    self._on_event(
                        KeyEvent(
                            ts=time.time(),
                            key=key,
                            vk=vk,
                            scan=scan,
                            extended=extended,
                            injected=bool(flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED)),
                        )
                    )
            except Exception:  # 回调内绝不允许抛异常
                pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)


# ---------------------------------------------------------------- 前台窗口


def process_name(pid: int) -> str:
    """由进程 ID 取可执行文件名（小写）。取不到返回空串。"""
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        path = buf.value
    finally:
        kernel32.CloseHandle(handle)
    if not path:
        return ""
    name = path.replace("/", "\\").rsplit("\\", 1)[-1]
    return name.lower()


def window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buf, length + 2)
    return buf.value


def window_class(hwnd: int) -> str:
    """窗口类名（小写）。用于识别控件类型。"""
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    if not user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256):
        return ""
    return buf.value.lower()


ES_PASSWORD = 0x0020
GWL_STYLE = -16


def focus_is_password_field() -> bool:
    """当前焦点控件是不是密码输入框。

    只认得出来标准 Windows 编辑控件的 ``ES_PASSWORD`` 样式——WinRAR / 好压 /
    系统凭据提示这类的原生对话框都能认出。浏览器、Electron、Qt 里的密码框是自绘的，
    这种样式查不到，所以按键浮层**还要**另外看隐私掩码状态，不能只靠这里。

    查不到就返回 False（由掩码兜底），不会因为误判而把按键显示出来。
    """
    try:
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
            return False
        hwnd = int(info.hwndFocus or 0)
        if not hwnd:
            return False
        if hasattr(user32, "GetWindowLongPtrW"):
            style = user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_STYLE)
        else:  # 32 位系统没有 *Ptr 版本
            style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE)
        if int(style) & ES_PASSWORD:
            return True
        return "password" in window_class(hwnd)
    except Exception:
        return False


def foreground_window() -> Optional[WindowInfo]:
    """当前前台窗口信息。无前台窗口时返回 None。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return WindowInfo(
        hwnd=int(hwnd),
        pid=int(pid.value),
        process=process_name(int(pid.value)),
        title=window_title(int(hwnd)),
    )


# ---------------------------------------------------------------- 修饰键状态

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12


def async_key_down(vk: int) -> bool:
    """某个键此刻是否处于按下状态（直接读硬件状态，不依赖消息队列）。

    用来在钩子回调里判断 Ctrl+V / Shift+Insert 这类组合键。
    """
    try:
        return bool(user32.GetAsyncKeyState(int(vk)) & 0x8000)
    except Exception:
        return False


def modifiers_down() -> Tuple[bool, bool, bool]:
    """返回 ``(Ctrl, Shift, Alt)`` 是否按下。"""
    return async_key_down(VK_CONTROL), async_key_down(VK_SHIFT), async_key_down(VK_MENU)


# ---------------------------------------------------------------- 空闲检测


def idle_seconds() -> float:
    """系统级"无输入"时长（秒），用于自动暂停记录。"""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    now = int(kernel32.GetTickCount())
    delta = now - int(info.dwTime)
    if delta < 0:  # GetTickCount 回绕
        delta += 0xFFFFFFFF
    return delta / 1000.0


# ---------------------------------------------------------------- 远程会话探测


def _visible_window_titles(limit: int = 400) -> List[Tuple[int, str]]:
    found: List[Tuple[int, str]] = []

    @EnumWindowsProc
    def _cb(hwnd, _lparam):
        if len(found) >= limit:
            return False
        if not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(int(hwnd))
        if title:
            found.append((int(hwnd), title))
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:
        return found
    return found


def detect_remote_session() -> Tuple[bool, str]:
    """判断当前是否存在远程控制会话。

    返回 ``(是否远程, 证据说明)``。证据来自可见窗口标题中的远程软件关键字。
    注意：这只用于给记录打标签（区分"我自己敲的"和"远程助手敲的"），
    不改变记录行为本身。
    """
    for _hwnd, title in _visible_window_titles():
        low = title.lower()
        for kw in REMOTE_TITLE_KEYWORDS:
            if kw in low:
                return True, title.strip()[:120]
    # 兜底：前台进程名命中远程软件
    fg = foreground_window()
    if fg and fg.process:
        stem = fg.process.rsplit(".", 1)[0].lower()
        if stem in REMOTE_PROCESS_NAMES:
            return True, REMOTE_APP_LABELS.get(stem, fg.process)
    return False, ""


def current_remote_app() -> str:
    """当前前台进程是不是远控 / 监控软件。

    是则返回友好显示名（如 ``向日葵``、``VNC``），否则返回空串。
    用于状态栏 / 按键浮层标注"远程输入"，比标题关键字更准（进程名不会误命中）。
    """
    try:
        fg = foreground_window()
        if fg and fg.process:
            stem = fg.process.rsplit(".", 1)[0].lower()
            if stem in REMOTE_PROCESS_NAMES:
                return REMOTE_APP_LABELS.get(stem, fg.process)
        if fg and fg.title:
            low = fg.title.lower()
            for kw in REMOTE_TITLE_KEYWORDS:
                if kw in low:
                    return REMOTE_APP_LABELS.get(kw, kw) if kw in REMOTE_APP_LABELS else kw
    except Exception:
        pass
    return ""


# 便于外部按需使用：把 ctypes 细节留在本模块内。
__all__ = [
    "KeyEvent",
    "WindowInfo",
    "KeyboardHook",
    "foreground_window",
    "process_name",
    "window_title",
    "window_class",
    "focus_is_password_field",
    "idle_seconds",
    "async_key_down",
    "modifiers_down",
    "detect_remote_session",
    "current_remote_app",
    "REMOTE_TITLE_KEYWORDS",
    "REMOTE_PROCESS_NAMES",
    "REMOTE_APP_LABELS",
    "LLKHF_INJECTED",
    "user32",
    "kernel32",
]
