"""剪贴板监听：登记"复制 / 粘贴"动作的发生、规模与归属，默认不保存正文。

为什么需要单独一块：
    键盘钩子只能看到 ``Ctrl+C`` / ``Ctrl+V`` 这两个键，看不出被复制粘贴的是
    一段文字、一张图还是一批文件，也看不出规模。剪贴板监听补上这部分：
    用 ``AddClipboardFormatListener`` 订阅 ``WM_CLIPBOARDUPDATE``，
    剪贴板内容一变就登记一次"复制"；粘贴则由按键组合（Ctrl+V / Shift+Insert）判定。

隐私边界（刻意设为保守）：
    * 默认**不保存**剪贴板正文，只保存内容类型（文本 / 图片 / 文件 / 空）、
      字符数与字节数、当时前台是哪个程序；
    * 命中隐私掩码（密码管理器、浏览器等）时连长度都不记，只记"发生过一次"；
    * 想保存正文必须手动把 ``config.json`` 里的 ``clipboard.store_text`` 改成
      ``true``，且最多只存 ``max_text_chars`` 个字符，掩码场景一律不存。
"""

from __future__ import annotations

import ctypes
import os
import struct
import tempfile
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, List, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- 常量

WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_TIMER = 0x0113

# 一次复制通常会连续触发多次 WM_CLIPBOARDUPDATE（先清空、再写入，
# 还可能有 DataObject / 位图等中间态）。等它静默一小会儿再读，
# 就能保证一次复制只登记一条，且读到的是最终内容。
_TIMER_ID = 1
_DEBOUNCE_MS = 120

HWND_MESSAGE = -3  # 只收消息、不显示的窗口
CLASS_NAME = "KeyLensClipboardListener"
WINDOW_NAME = "KeyLens 剪贴板监听窗口（隐藏）"

CF_TEXT = 1
CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_OEMTEXT = 7
CF_DIB = 8
CF_UNICODETEXT = 13
CF_HDROP = 15
CF_DIBV5 = 17

GMEM_MOVEABLE = 0x0002

_IMAGE_FORMATS = (CF_BITMAP, CF_DIB, CF_DIBV5)

# 常见标准格式的名字；自定义格式用 GetClipboardFormatNameW 现取（如 "HTML Format"）。
_STANDARD_FORMATS = {
    CF_TEXT: "CF_TEXT",
    CF_BITMAP: "CF_BITMAP",
    CF_METAFILEPICT: "CF_METAFILEPICT",
    CF_OEMTEXT: "CF_OEMTEXT",
    CF_DIB: "CF_DIB",
    CF_UNICODETEXT: "CF_UNICODETEXT",
    CF_HDROP: "CF_HDROP",
    CF_DIBV5: "CF_DIBV5",
}

# 剪贴板可能被塞进超大内容（整本日志、巨型位图），这里只取前若干个格式、只量长度。
MAX_FORMATS = 8
MAX_MEASURE_CHARS = 400_000  # 超过这个长度就只按字节估算，不再实例化字符串

# 内容类型 -> 界面/报告里的显示名
CONTENT_DISPLAY = {
    "text": "文本",
    "image": "图片",
    "files": "文件",
    "empty": "空",
    "other": "其他",
    "unknown": "未知（没取到剪贴板快照）",
    "MASKED": "掩码（未记录内容）",
}

# 粘贴快捷键：Ctrl+V、Ctrl+Shift+V、Shift+Insert
PASTE_KEYS = ("V", "INSERT")


def is_paste_combo(key: str, ctrl: bool, shift: bool) -> bool:
    """判断一次按键是否构成"粘贴"动作（与剪贴板内容无关，只看组合键）。

    放在这里是为了能被自检直接调用：它不依赖任何 Win32 状态，输入输出都是纯数据。
    """
    if key == "V":
        return bool(ctrl)
    if key == "INSERT":
        return bool(shift) and not bool(ctrl)
    return False


def is_copy_combo(key: str, ctrl: bool) -> bool:
    """判断一次按键是否为常见的复制 / 剪切组合（Ctrl+C / Ctrl+X）。

    注意：真正的"复制成功"以剪贴板内容变化为准，这里只用于给事件补一个触发来源。
    """
    return bool(ctrl) and key in ("C", "X")


# ---------------------------------------------------------------- 结构体与签名

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
user32.AddClipboardFormatListener.restype = wintypes.BOOL
user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
user32.RemoveClipboardFormatListener.restype = wintypes.BOOL

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardOwner.restype = wintypes.HWND
user32.CountClipboardFormats.restype = ctypes.c_int
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
user32.GetClipboardFormatNameW.restype = ctypes.c_int

user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.KillTimer.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.restype = wintypes.BOOL


# ---------------------------------------------------------------- 数据类


@dataclass(frozen=True)
class ClipSnapshot:
    """一次剪贴板内容的"体检结果"：只有类型与规模，没有正文。"""

    ts: float
    content: str  # text / image / files / empty / other
    formats: str  # 格式名列表，逗号分隔（最多 MAX_FORMATS 个）
    text_len: int  # 字符数；非文本或超长估算时为 0 / 估算值
    byte_len: int  # 剪贴板数据占用字节数（估算）
    preview: str = ""  # 仅当调用方要求取正文预览时非空（默认不取）
    saved_path: str = ""  # 复制图片时保存的 .bmp 文件路径（未启用保存时为空）

    @property
    def content_display(self) -> str:
        return CONTENT_DISPLAY.get(self.content, self.content)


# ---------------------------------------------------------------- 读取剪贴板


def format_name(fmt: int) -> str:
    """格式号 -> 可读名字。自定义格式（HTML Format 等）现取。"""
    if fmt in _STANDARD_FORMATS:
        return _STANDARD_FORMATS[fmt]
    buf = ctypes.create_unicode_buffer(128)
    length = user32.GetClipboardFormatNameW(fmt, buf, 128)
    if length > 0 and buf.value:
        return buf.value
    return "FMT_%d" % fmt


def classify(formats: List[int]) -> str:
    """按格式集合判断内容类型。"""
    if not formats:
        return "empty"
    if CF_HDROP in formats:
        return "files"
    if any(f in _IMAGE_FORMATS for f in formats):
        return "image"
    if CF_UNICODETEXT in formats or CF_TEXT in formats or CF_OEMTEXT in formats:
        return "text"
    return "other"


def _text_length(handle) -> Tuple[int, int]:
    """量一段 CF_UNICODETEXT 的字符数与字节数。返回 (字符数, 字节数)。"""
    size = int(kernel32.GlobalSize(handle) or 0)
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return 0, size
    try:
        units = size // 2
        if units <= 0:
            return 0, size
        if units > MAX_MEASURE_CHARS:
            # 超大内容不实例化字符串，按字节估算字符数。
            return units, size
        text = ctypes.wstring_at(ptr, units)
    finally:
        kernel32.GlobalUnlock(handle)
    return len(text.rstrip("\0")), size


def image_save_dir() -> str:
    """剪贴板图片临时文件的统一保存目录（%TEMP%\\KeyLens-clips）。"""
    return os.path.join(tempfile.gettempdir(), "KeyLens-clips")


def _dib_bytes(handle) -> bytes:
    """读出 CF_DIB 句柄指向的完整 DIB 数据。"""
    size = int(kernel32.GlobalSize(handle) or 0)
    ptr = kernel32.GlobalLock(handle)
    if not ptr or size <= 0:
        if ptr:
            kernel32.GlobalUnlock(handle)
        return b""
    try:
        return ctypes.string_at(ptr, size)
    finally:
        kernel32.GlobalUnlock(handle)


def _dib_to_bmp(dib: bytes) -> bytes:
    """给 DIB 数据加 BITMAPFILEHEADER，变成可直接打开的 .bmp。"""
    if len(dib) < 40:
        return b""
    bi_size, _w, _h, _planes, bitcount, compression, *_ = struct.unpack_from("<IiiHHII", dib)
    off = 14 + bi_size
    if compression == 0 and 0 < bitcount <= 8:
        off += (1 << bitcount) * 4  # BI_RGB 调色板
    elif compression == 3 and bi_size == 40:
        off += 12  # BI_BITFIELDS 的三个掩码（V4/V5 头自带，不用加）
    header = struct.pack("<2sIHHI", b"BM", 14 + len(dib), 0, 0, off)
    return header + dib


def _save_dib(handle, save_dir: str) -> str:
    """把剪贴板里的 DIB 写成 .bmp 临时文件，返回路径；失败返回空串。"""
    try:
        bmp = _dib_to_bmp(_dib_bytes(handle))
        if not bmp:
            return ""
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, "clip-%s.bmp" % time.strftime("%Y%m%d-%H%M%S"))
        with open(path, "wb") as fh:
            fh.write(bmp)
        return path
    except Exception:
        return ""


def read_clipboard(peek_chars: int = 0, save_dir: str = "") -> Optional[ClipSnapshot]:
    """打开剪贴板读一次"类型 + 规模"。

    ``peek_chars > 0`` 时额外取前 N 个字符放进 ``preview``（只在用户显式开启
    保存正文时才会传）。``save_dir`` 非空时把图片内容存成 .bmp 临时文件。
    打不开剪贴板（被别的程序占用）时返回 None。
    """
    if not user32.OpenClipboard(None):
        return None
    try:
        formats: List[int] = []
        fmt = 0
        while len(formats) < MAX_FORMATS:
            fmt = int(user32.EnumClipboardFormats(fmt))
            if not fmt:
                break
            formats.append(fmt)

        names = ", ".join(format_name(f) for f in formats[:6])
        content = classify(formats)

        text_len = 0
        byte_len = 0
        preview = ""
        saved_path = ""
        try:
            if CF_UNICODETEXT in formats:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if handle:
                    text_len, byte_len = _text_length(handle)
                    if peek_chars > 0 and text_len > 0:
                        ptr = kernel32.GlobalLock(handle)
                        if ptr:
                            try:
                                units = min(text_len, peek_chars)
                                preview = ctypes.wstring_at(ptr, units)
                            finally:
                                kernel32.GlobalUnlock(handle)
            elif CF_HDROP in formats or any(f in _IMAGE_FORMATS for f in formats):
                # 图片优先取 DIB / DIBV5（CF_BITMAP 句柄读不到像素数据）
                dib_fmt = CF_DIB if CF_DIB in formats else (CF_DIBV5 if CF_DIBV5 in formats else 0)
                handle = user32.GetClipboardData(dib_fmt or formats[0]) if formats else None
                if handle:
                    byte_len = int(kernel32.GlobalSize(handle) or 0)
                    if save_dir and dib_fmt:
                        saved_path = _save_dib(handle, save_dir)
        except Exception:
            pass
        return ClipSnapshot(
            ts=time.time(),
            content=content,
            formats=names,
            text_len=text_len,
            byte_len=byte_len,
            preview=preview,
            saved_path=saved_path,
        )
    except Exception:
        return None
    finally:
        user32.CloseClipboard()


def set_clipboard_image(path: str) -> bool:
    """把一个 .bmp 文件重新放回剪贴板（CF_DIB），供再次粘贴使用。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return False
    if len(data) <= 14 or data[:2] != b"BM":
        return False
    dib = data[14:]
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
        if not mem:
            return False
        ptr = kernel32.GlobalLock(mem)
        if not ptr:
            kernel32.GlobalFree(mem)
            return False
        try:
            ctypes.memmove(ptr, dib, len(dib))
        finally:
            kernel32.GlobalUnlock(mem)
        if not user32.SetClipboardData(CF_DIB, mem):
            kernel32.GlobalFree(mem)
            return False
        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- 监听线程


class ClipboardWatcher:
    """剪贴板变化监听。

    与键盘钩子同构：独立线程 + 只收消息的隐藏窗口 + 标准消息循环。
    收到 ``WM_CLIPBOARDUPDATE`` 时读一次剪贴板，把结果交给 ``on_change``。
    """

    def __init__(
        self,
        on_change: Callable[[ClipSnapshot], None],
        on_error: Optional[Callable[[str], None]] = None,
        peek_chars: int = 0,
        save_dir: str = "",
    ):
        self._on_change = on_change
        self._on_error = on_error
        self._peek_chars = max(0, int(peek_chars))
        self._save_dir = save_dir or ""
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._hwnd = None
        self._ready = threading.Event()
        self._running = False
        self._error: Optional[str] = None
        self._proc = WNDPROC(self._wnd_proc)  # 保持引用，防止被 GC
        self._instance = None
        self._last_update = 0.0

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def running(self) -> bool:
        return self._running

    def start(self, timeout: float = 5.0) -> bool:
        if self._running:
            return True
        if not hasattr(user32, "AddClipboardFormatListener"):
            self._error = "当前系统不支持 AddClipboardFormatListener（需 Windows Vista 以上）"
            if self._on_error:
                self._on_error(self._error)
            return False
        self._thread = threading.Thread(target=self._run, name="keylens-clipboard", daemon=True)
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
        self._hwnd = None
        self._thread = None
        self._thread_id = None
        self._ready.clear()

    # -- 内部实现 -------------------------------------------------

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._instance = kernel32.GetModuleHandleW(None)

        wnd_class = WNDCLASSW()
        wnd_class.lpfnWndProc = self._proc
        wnd_class.hInstance = self._instance
        wnd_class.lpszClassName = CLASS_NAME
        atom = user32.RegisterClassW(ctypes.byref(wnd_class))
        if not atom and ctypes.get_last_error() != 1410:  # 1410=类已存在，可复用
            self._fail("RegisterClassW 失败，错误码 %d" % ctypes.get_last_error())
            return

        self._hwnd = user32.CreateWindowExW(
            0, CLASS_NAME, WINDOW_NAME, 0,
            0, 0, 0, 0,
            wintypes.HWND(HWND_MESSAGE), None, self._instance, None,
        )
        if not self._hwnd:
            self._fail("CreateWindowExW 失败，错误码 %d" % ctypes.get_last_error())
            return
        if not user32.AddClipboardFormatListener(self._hwnd):
            self._fail("AddClipboardFormatListener 失败，错误码 %d" % ctypes.get_last_error())
            return

        self._running = True
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._cleanup()
            self._running = False

    def _cleanup(self) -> None:
        try:
            if self._hwnd:
                user32.RemoveClipboardFormatListener(self._hwnd)
                user32.DestroyWindow(self._hwnd)
        except Exception:
            pass
        try:
            if self._instance:
                user32.UnregisterClassW(CLASS_NAME, self._instance)
        except Exception:
            pass
        self._hwnd = None

    def _fail(self, message: str) -> None:
        self._error = message
        self._cleanup()
        if self._on_error:
            self._on_error(message)
        self._ready.set()

    def _wnd_proc(self, hwnd, message, w_param, l_param):  # noqa: N802 (Win32 命名)
        if message == WM_CLIPBOARDUPDATE and self._running:
            # 不在这里读：一次复制会连着发好几条更新，直接读会读到中间态，
            # 也会把一次复制记成好几次。改为起一个短定时器，等它静默后再读。
            self._last_update = time.time()
            try:
                user32.SetTimer(self._hwnd, _TIMER_ID, _DEBOUNCE_MS, None)
            except Exception:
                pass
            return 0
        if message == WM_TIMER and int(w_param) == _TIMER_ID and self._running:
            quiet_ms = (time.time() - self._last_update) * 1000.0
            if quiet_ms >= _DEBOUNCE_MS * 0.8:
                try:
                    user32.KillTimer(self._hwnd, _TIMER_ID)
                except Exception:
                    pass
                # 某些程序复制时会短暂锁住剪贴板，导致一次读失败；重试几次。
                snapshot = None
                for _ in range(3):
                    try:
                        snapshot = read_clipboard(peek_chars=self._peek_chars, save_dir=self._save_dir)
                    except Exception:
                        pass
                    if snapshot is not None:
                        break
                    time.sleep(0.05)
                # 重试后仍读不到，也记录一次"剪贴板更新但内容未知"，
                # 这样后续粘贴至少能关联到这次复制动作，不会完全丢失。
                if snapshot is None:
                    snapshot = ClipSnapshot(
                        ts=time.time(),
                        content="unknown",
                        formats="",
                        text_len=0,
                        byte_len=0,
                        preview="",
                    )
                if self._on_change:
                    try:
                        self._on_change(snapshot)
                    except Exception:
                        pass
            return 0
        if message == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, message, w_param, l_param)


__all__ = [
    "ClipSnapshot",
    "ClipboardWatcher",
    "read_clipboard",
    "set_clipboard_image",
    "classify",
    "format_name",
    "is_paste_combo",
    "is_copy_combo",
    "CONTENT_DISPLAY",
    "PASTE_KEYS",
]
