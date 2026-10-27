"""系统托盘图标 + 全局热键（默认 Ctrl+Alt+K，可自定义）切换主界面显示 / 隐藏。

纯 Win32 实现（Shell_NotifyIconW + RegisterHotKey），与剪贴板监听同构：
独立线程 + 只收消息的隐藏窗口 + 标准消息循环。鼠标 / 热键事件通过回调
抛出；回调在**托盘线程**里执行，调用方必须自己转回 Tk 主线程。

图标优先加载项目里的 app.ico，找不到时用 Pillow 现画兜底，再退回系统默认图标。
托盘提示文字（tooltip）里带上当前快捷键，运行中可通过 set_hotkey /
set_tooltip 实时更新（热键换绑、tooltip 改字都走托盘线程消息完成）。
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import threading
from ctypes import wintypes
from typing import Callable, Optional

from .. import __version__

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 显式声明 64 位下有截断/溢出风险的函数签名
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]

# ---------------------------------------------------------------- 常量

WM_APP_TRAY = 0x8000  # WM_APP 起点：托盘回调消息
WM_APP_UPDATE = 0x8001  # WM_APP_TRAY + 1：主线程请求改 tooltip / 换热键
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_DESTROY = 0x0002

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
IDI_APPLICATION = 32512

MOD_ALT = 0x1
MOD_CONTROL = 0x2
MOD_SHIFT = 0x4
MOD_WIN = 0x8
SMTO_BLOCK = 0x1
HOTKEY_ID = 1

DEFAULT_HOTKEY = "Ctrl+Alt+K"

# 修饰键名 → RegisterHotKey 位掩码
_MOD_FLAGS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}
# Tk/常见修饰键别名 → 统一显示名（tooltip 与菜单里用这套写法）
_MOD_DISPLAY = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift",
                "win": "Win", "super": "Win", "meta": "Win"}


def _normalize_hotkey(text: str) -> str:
    """把 "ctrl+alt+k" 之类归一成 "Ctrl+Alt+K" 风格的显示文本。"""
    parts = [p.strip() for p in str(text or "").split("+") if p.strip()]
    if not parts:
        return ""
    names = [_MOD_DISPLAY.get(p.lower(), p.upper() if len(p) == 1 else p) for p in parts]
    return "+".join(names)


def parse_hotkey(text: str) -> Optional[tuple]:
    """把 "Ctrl+Alt+K" 解析成 (修饰键位掩码, 虚拟键码)；不合法返回 None。

    支持 Ctrl / Alt / Shift / Win 修饰（至少要有一个）+ 单个字母、数字
    或 F1~F24 功能键。热键必须带修饰键，否则会把普通打字全部劫持。
    """
    parts = [p.strip() for p in str(text or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for name in parts[:-1]:
        flag = _MOD_FLAGS.get(name.lower())
        if not flag or mods & flag:
            return None
        mods |= flag
    key = parts[-1]
    if len(key) == 1:
        if key.isdigit():
            vk = ord(key)
        elif "a" <= key.lower() <= "z":
            vk = ord(key.upper())
        else:
            return None
    else:
        lower = key.lower()
        if lower[0] == "f" and lower[1:].isdigit():
            number = int(lower[1:])
            if not 1 <= number <= 24:
                return None
            vk = 0x70 + number - 1  # VK_F1 = 0x70
        else:
            return None
    return mods, vk


def default_tooltip(hotkey: str = DEFAULT_HOTKEY) -> str:
    # 带版本号：托盘提示是最容易被人看到的地方，出问题时能直接对上版本
    return "KeyLens 按键记录器 v%s（左键显示/隐藏 · %s）" % (__version__, hotkey)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# ---------------------------------------------------------------- 图标


def _find_app_ico() -> str:
    """定位应用图标 app.ico：包目录 → tools 目录 → 解包目录 → exe 同目录。"""
    here = os.path.dirname(os.path.abspath(__file__))
    pkg_dir = os.path.dirname(here)
    candidates = [
        os.path.join(pkg_dir, "app.ico"),
        os.path.join(os.path.dirname(pkg_dir), "tools", "app.ico"),
        os.path.join(getattr(sys, "_MEIPASS", "") or "", "app.ico"),
        os.path.join(os.path.dirname(getattr(sys, "executable", "") or ""), "app.ico"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def _make_icon_file() -> str:
    """用 Pillow 画一个兜底 .ico 临时文件（找不到 app.ico 时才用），返回路径。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    accent = (74, 163, 255, 255)
    draw.rounded_rectangle((4, 8, 28, 26), radius=5, fill=accent)
    draw.rectangle((14, 2, 18, 10), fill=accent)
    path = os.path.join(tempfile.gettempdir(), "keylens-tray.ico")
    image.save(path, "ICO")
    return path


def _load_hicon() -> Optional[int]:
    """加载托盘 HICON：优先真正的 app.ico，其次现画兜底，最后系统默认。"""
    paths = [path for path in (_find_app_ico(),) if path]
    if not paths:
        try:
            paths.append(_make_icon_file())
        except Exception:
            pass
    for path in paths:
        try:
            handle = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
            if handle:
                return int(handle)
        except Exception:
            pass
    return int(user32.LoadIconW(None, IDI_APPLICATION) or 0)


# ---------------------------------------------------------------- 托盘


class TrayIcon:
    """托盘图标 + 全局热键。

    回调（都在托盘线程里执行，调用方需转回主线程）：
    * ``on_toggle()`` —— 左键单击 / 双击 / 全局热键：切换主界面显示与隐藏
    * ``on_menu(x, y)`` —— 右键：在鼠标位置弹出主线程菜单
    * ``on_quit()`` —— 菜单里的"退出"

    ``hotkey`` 为 "Ctrl+Alt+K" 风格的组合键文本（必须带修饰键），
    ``tooltip`` 缺省时按热键自动生成；运行中可用 set_hotkey / set_tooltip 更新。
    """

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_menu: Callable[[int, int], None],
        on_quit: Callable[[], None],
        hotkey: str = DEFAULT_HOTKEY,
        tooltip: Optional[str] = None,
    ):
        self._on_toggle = on_toggle
        self._on_menu = on_menu
        self._on_quit = on_quit
        self._hotkey = _normalize_hotkey(hotkey) or DEFAULT_HOTKEY
        self._tooltip = tooltip or default_tooltip(self._hotkey)
        self._pending_op: Optional[tuple] = None
        self._op_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._hwnd = None
        self._hicon = 0
        self._ready = threading.Event()
        self._running = False
        self._error: Optional[str] = None
        self._proc = WNDPROC(self._wnd_proc)  # 保持引用，防止被 GC
        self._instance = None

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def running(self) -> bool:
        return self._running

    @property
    def hotkey(self) -> str:
        """当前生效的全局热键文本；未注册成功时为空串。"""
        return self._hotkey

    # -- 运行中更新（主线程调用，内部转发到托盘线程） -------------

    def set_tooltip(self, text: str) -> bool:
        """更新托盘悬停提示。返回 False 表示托盘未在运行或已无响应。"""
        return self._call_tray("tooltip", str(text or ""))

    def set_hotkey(self, hotkey: str) -> bool:
        """换绑全局热键。返回 False 表示托盘未在运行或已无响应；
        热键本身是否注册成功看随后的 ``hotkey`` 属性（被占用时会清空）。"""
        return self._call_tray("hotkey", _normalize_hotkey(hotkey))

    def _call_tray(self, kind: str, value: str) -> bool:
        """把一次更新请求投递给托盘线程同步执行（SendMessage 转回结果）。"""
        if not self._running or not self._hwnd:
            return False
        with self._op_lock:
            self._pending_op = (kind, value)
            try:
                result = wintypes.DWORD()
                ok = user32.SendMessageTimeoutW(
                    self._hwnd, WM_APP_UPDATE, 0, 0, SMTO_BLOCK, 2000,
                    ctypes.byref(result))
                return bool(ok)
            except Exception:
                return False
            finally:
                self._pending_op = None

    def start(self, timeout: float = 5.0) -> bool:
        if self._running:
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="KeyLensTray", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self._error = "托盘线程启动超时"
            return False
        return self._error is None

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=3.0)
        self._thread = None
        self._hwnd = None

    # -- 线程主体 -------------------------------------------------

    def _run(self) -> None:
        try:
            self._run_inner()
        except Exception as exc:  # 托盘线程绝不让进程崩溃
            self._fail("托盘异常：%r" % (exc,))

    def _run_inner(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._instance = kernel32.GetModuleHandleW(None)

        wnd_class = WNDCLASSW()
        wnd_class.lpfnWndProc = self._proc
        wnd_class.hInstance = self._instance
        wnd_class.lpszClassName = "KeyLensTrayListener"
        if not user32.RegisterClassW(ctypes.byref(wnd_class)):
            self._fail("托盘窗口类注册失败")
            return

        # 只收消息的隐藏窗口：承载托盘回调与热键消息
        self._hwnd = user32.CreateWindowExW(
            0, wnd_class.lpszClassName, "KeyLens 托盘", 0,
            0, 0, 0, 0, None, None, self._instance, None,
        )
        if not self._hwnd:
            self._fail("托盘消息窗口创建失败")
            return

        self._hicon = _load_hicon()
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_APP_TRAY
        data.hIcon = self._hicon
        data.szTip = self._tooltip[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            self._fail("托盘图标注册失败（可能被系统策略拦截）")
            return

        # 全局热键（可自定义，如 Ctrl+Alt+K）；解析失败回落默认组合
        self._register_hotkey(self._hotkey)

        self._running = True
        self._error = None
        self._ready.set()

        msg = wintypes.MSG()
        while self._running:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # 收尾：摘掉托盘图标、注销热键
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        user32.UnregisterHotKey(self._hwnd, HOTKEY_ID)
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
        self._hwnd = None

    def _fail(self, message: str) -> None:
        self._error = message
        self._running = False
        self._ready.set()

    # -- 托盘线程内执行（由 _wnd_proc 在 WM_APP_UPDATE 时调用） ----

    def _register_hotkey(self, hotkey: str) -> None:
        """按文本注册全局热键；失败只清空记录，不致命（左键仍可切换）。"""
        parsed = parse_hotkey(hotkey)
        if parsed and user32.RegisterHotKey(self._hwnd, HOTKEY_ID, parsed[0], parsed[1]):
            self._hotkey = _normalize_hotkey(hotkey)
        else:
            self._hotkey = ""

    def _apply_tooltip(self, text: str) -> None:
        self._tooltip = text[:127]
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_TIP
        data.szTip = self._tooltip
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _apply_hotkey(self, hotkey: str) -> None:
        user32.UnregisterHotKey(self._hwnd, HOTKEY_ID)
        self._register_hotkey(hotkey)

    def _wnd_proc(self, hwnd, message, w_param, l_param):  # noqa: N802 (Win32 命名)
        if message == WM_APP_UPDATE:
            op = self._pending_op
            if op:
                kind, value = op
                if kind == "tooltip":
                    self._apply_tooltip(value)
                elif kind == "hotkey":
                    self._apply_hotkey(value)
            return 1
        if message == WM_APP_TRAY and self._running:
            event = int(l_param) & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._on_toggle()
            elif event == WM_RBUTTONUP:
                point = POINT()
                user32.GetCursorPos(ctypes.byref(point))
                self._on_menu(int(point.x), int(point.y))
            return 0
        if message == WM_HOTKEY and self._running and int(w_param) == HOTKEY_ID:
            self._on_toggle()
            return 0
        if message == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, message, w_param, l_param)


__all__ = ["TrayIcon", "DEFAULT_HOTKEY", "default_tooltip", "parse_hotkey"]
