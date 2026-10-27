"""采集调度：把钩子事件落库、维护会话、执行隐私掩码、维护实时计数。

线程模型：
  * 钩子线程（winapi.KeyboardHook）—— 只解析并调用 :meth:`Tracker.on_key`；
  * 写入线程 —— 从队列批量取事件，批量写 SQLite；
  * 维护线程 —— 空闲自动暂停、远程会话探测、保留期清理、会话收尾；
  * 界面线程 —— 只读 :meth:`Tracker.live_stats`，不做任何 IO。
"""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import clipboard, keymap, winapi
from .db import ClipRecord, Database, EventRecord, day_str, default_data_dir

DEFAULT_CONFIG_NAME = "config.json"


# ---------------------------------------------------------------- 配置


@dataclass
class PrivacyConfig:
    """隐私掩码：命中时只记录"按了键"，不记录是哪个键。"""

    enabled: bool = True
    # 密码 / 凭据类软件：默认全部掩码（浏览器地址栏与登录框无法从窗口标题区分）
    password_apps: List[str] = field(
        default_factory=lambda: [
            "1password.exe",
            "bitwarden.exe",
            "keepass.exe",
            "keepassxc.exe",
            "lastpass.exe",
            "dashlane.exe",
            "enpass.exe",
            "nordpass.exe",
            "keeper.exe",
            "roboform.exe",
            "chrome.exe",
            "msedge.exe",
            "firefox.exe",
            "iexplore.exe",
            "brave.exe",
            "opera.exe",
            "vivaldi.exe",
        ]
    )
    # 会让你输入口令的普通软件：解压 / 加密容器这类。
    # 掩码后只累加"按了几次"，不记键位、不记窗口标题，口令没有落库的路径。
    mask_processes: List[str] = field(
        default_factory=lambda: [
            "winrar.exe",
            "rar.exe",
            "7zfm.exe",
            "7zg.exe",
            "7z.exe",
            "haozip.exe",
            "haozipc.exe",
            "2345zip.exe",
            "360zip.exe",
            "bandizip.exe",
            "peazip.exe",
            "winzip.exe",
            "winzip64.exe",
            "veracrypt.exe",
            "truecrypt.exe",
            "axcrypt.exe",
        ]
    )
    title_keywords: List[str] = field(
        default_factory=lambda: [
            "password",
            "passwd",
            "密码",
            "口令",
            "凭据",
            "credential",
            "钱包",
            "wallet",
            "身份验证器",
            "authenticator",
            "支付",
            "网银",
            "登录 - ",
            "sign in",
            "log in",
            # 解压 / 加密容器这类软件的口令对话框
            "解压",
            "解压密码",
            "输入口令",
            "加密",
            "解密",
            "archive",
            "decrypt",
        ]
    )
    # 从内置默认掩码名单里放开的进程。
    # 比如你确实想在浏览器里看按键浮层，可以把 "chrome.exe" 写到这里——
    # 代价是浏览器里的输入（含密码框）不再受掩码保护，请自己权衡。
    allow_unmask: List[str] = field(default_factory=list)
    # 剪贴板是否沿用"按键那样严格"的掩码名单（浏览器等进程前台就一律不记内容）。
    # 默认 False：只有标题出现密码/口令、或前台是密码管理器时才拦，
    # 这样在浏览器里正常复制网页文字仍能看到内容。
    clipboard_mask_strict: bool = False
    # 是否对"解压 / 加密容器"整进程掩码（WinRAR、7-Zip、VeraCrypt… 以及
    # 标题里的 解压 / 解密 / archive 等关键字）。
    # 默认 False：这些软件里输入的口令照常显示在时间线里；
    # 想要更严的保护就设为 true（启动时会自动补回内置名单，见 merge_mask_lists）。
    strict_archive_mask: bool = False


# 浏览器进程：它们在 password_apps 里是为了保护"按键"，但剪贴板不该连坐——
# 用户主动在网页里复制一段文字是很常见的操作，不该因为前台是浏览器就被拦掉。
_BROWSER_PROCESSES = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "iexplore.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "safari.exe", "360se.exe", "360ee.exe",
    "sogouexplorer.exe", "maxthon.exe", "theworld.exe", "qqbrowser.exe",
})


@dataclass
class ClipboardConfig:
    """复制 / 粘贴的记录开关。

    默认只登记"发生了复制 / 粘贴"以及内容类型与规模，**不保存正文**。
    ``store_text`` 一旦打开，被复制的文本前 ``max_text_chars`` 个字符会落库，
    请确认这是你自己的设备、且清楚剪贴板里经常有口令类内容。
    """

    enabled: bool = True
    track_copy: bool = True  # 剪贴板内容变化（复制 / 剪切 / 程序写入）
    track_paste: bool = True  # Ctrl+V / Shift+Insert 等粘贴组合键
    store_text: bool = False  # 保存正文（默认关；掩码场景一律不存）
    max_text_chars: int = 80
    save_images: bool = True  # 复制图片时把图片存成临时 .bmp，界面里可预览/放回剪贴板


@dataclass
class KeyPreview:
    """给按键浮层用的实时事件。

    **只走内存**：不落库、不写文件、不进任何统计，浮层关掉就没了。
    带的是"按了哪个键"（键位标识，如 ``Ctrl + S``），不是"输入了什么字符"。
    """

    key: str
    masked: bool
    injected: bool
    ctrl: bool
    shift: bool
    alt: bool
    ts: float

    @property
    def label(self) -> str:
        if self.masked:
            return "隐私掩码 · 不记录内容"
        parts: List[str] = []
        if self.ctrl:
            parts.append("Ctrl")
        if self.shift:
            parts.append("Shift")
        if self.alt:
            parts.append("Alt")
        parts.append(keymap.display(self.key))
        return " + ".join(parts)


@dataclass
class Config:
    data_dir: str = field(default_factory=default_data_dir)
    pause_when_idle_seconds: float = 300.0  # 无输入超过该秒数后停止记录
    session_gap_seconds: float = 300.0  # 超过该空隙视为新会话
    batch_size: int = 200
    # 落库节奏：越小越实时（界面时间线越快看到新记录），代价是 SQLite 写入次数变多。
    # 0.5 秒一批在 WAL 模式下开销很低，同时把写入线程的空转唤醒压到 4Hz。
    batch_interval: float = 0.5
    remote_poll_seconds: float = 5.0
    retention_days: int = 90
    store_window_titles: bool = True  # 关掉则窗口标题一律不入库
    start_paused: bool = False
    # 按键浮层只显示按键本身（背景透明、不显示标题与说明行）。
    # 默认开启：浮层是贴边的小挂件，带标题栏和关闭按钮反而像个独立窗口。
    # 想恢复完整面板（含标题、×、说明行），在设置里取消勾选即可。
    overlay_keys_only: bool = True
    toggle_hotkey: str = "Ctrl+Alt+K"  # 托盘显示/隐藏主界面的全局快捷键（可自定义）
    ui_mode: str = "dark"  # 界面配色主题："dark" / "light"
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    clipboard: ClipboardConfig = field(default_factory=ClipboardConfig)

    # -- 读写 -----------------------------------------------------

    @property
    def config_path(self) -> str:
        return os.path.join(self.data_dir, DEFAULT_CONFIG_NAME)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "keylens.db")

    def save(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, data_dir: Optional[str] = None) -> "Config":
        cfg = cls()
        if data_dir:
            cfg.data_dir = data_dir
        path = os.path.join(cfg.data_dir, DEFAULT_CONFIG_NAME)
        if not os.path.isfile(path):
            return cfg
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return cfg
        apply_mapping(cfg, raw)
        merge_mask_lists(cfg.privacy)
        # 旧配置里存着 batch_interval=2.0（老默认值），会让界面时间线慢半拍。
        # 这里统一收敛到新默认值；用户手动调得更小的值不受影响。
        if cfg.batch_interval > 1.0:
            cfg.batch_interval = Config.batch_interval
        return cfg


# 掩码名单：新增的内置默认项要能自动生效，所以做一次并集。
MASK_LIST_FIELDS = ("password_apps", "mask_processes", "title_keywords")

# 压缩 / 加密容器相关项：这些只用来兜住 WinRAR、7-Zip 这类软件的口令对话框。
# 用户希望在这里输入的口令能显示出来，所以默认不再掩码；
# 把 privacy.strict_archive_mask 设为 true 就恢复内置兜底（含旧 config.json 的自愈）。
ARCHIVE_APPS = frozenset({
    "winrar.exe", "rar.exe", "7zfm.exe", "7zg.exe", "7z.exe", "haozip.exe",
    "haozipc.exe", "2345zip.exe", "360zip.exe", "bandizip.exe", "peazip.exe",
    "winzip.exe", "winzip64.exe", "veracrypt.exe", "truecrypt.exe", "axcrypt.exe",
})
ARCHIVE_KEYWORDS = frozenset({
    "解压", "解压密码", "输入口令", "加密", "解密", "archive", "decrypt",
})


def merge_mask_lists(privacy: PrivacyConfig) -> None:
    """把内置的掩码默认项并进配置里，用户在 config.json 里加的项保留。

    为什么是并集：掩码是安全方向的设置。以后新增的默认掩码项（比如某款解压软件）
    应该自动生效，而不是因为你手上有一份旧配置就悄悄漏掉。
    代价：内置项没法从 config.json 里删掉；确实要放开，写进 ``allow_unmask``。

    例外：压缩 / 加密容器（WinRAR、7-Zip…）及其标题关键字默认**不**并入，
    这样在它们里面输入的口令会照常显示；``privacy.strict_archive_mask = true``
    可以把它们重新并回来（对本机旧配置同样生效）。
    """
    defaults = PrivacyConfig()
    allowed = {str(name).lower() for name in privacy.allow_unmask}
    strict_archive = bool(privacy.strict_archive_mask)
    for name in MASK_LIST_FIELDS:
        current = list(getattr(privacy, name) or [])
        base = [item for item in (getattr(defaults, name) or []) if str(item).lower() not in allowed]
        merged = list(dict.fromkeys(base + current))
        if not strict_archive:
            # 对"合并结果"统一过滤：内置默认项、以及旧 config.json 里残留的同一批项
            # 都会被清掉（这就是旧配置的自愈），想保留就把 strict_archive_mask 设为 true。
            blocked = ARCHIVE_KEYWORDS if name == "title_keywords" else (
                ARCHIVE_APPS if name == "mask_processes" else frozenset())
            merged = [item for item in merged if str(item).lower() not in blocked]
        setattr(privacy, name, merged)


def apply_mapping(target: Any, raw: Dict[str, object]) -> None:
    """把配置字典套到 dataclass 上；嵌套的子配置（privacy / clipboard）递归处理。

    只认已经在 dataclass 里声明过的字段，配置文件里多出来的键一律忽略，
    避免旧版本遗留项把运行时配置搞乱。
    """
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        current = getattr(target, key, None)
        if current is None and not hasattr(target, key):
            continue
        if isinstance(value, dict) and is_dataclass(current):
            apply_mapping(current, value)
        else:
            try:
                setattr(target, key, value)
            except Exception:
                pass


# ---------------------------------------------------------------- 实时统计


@dataclass
class LiveStats:
    total_keys: int = 0
    session_keys: int = 0
    masked_keys: int = 0
    keys_last_minute: int = 0
    active_minutes: float = 0.0
    top_keys: List[tuple] = field(default_factory=list)  # [(键位, 次数)]
    top_processes: List[tuple] = field(default_factory=list)
    session_id: Optional[int] = None
    session_started: Optional[float] = None
    remote: bool = False
    remote_note: str = ""
    paused: bool = False
    paused_reason: str = ""
    masking_now: bool = False
    clip_copy: int = 0
    clip_paste: int = 0
    clip_masked: int = 0
    clipboard_active: bool = False
    clipboard_error: str = ""
    db_path: str = ""
    db_size: int = 0


# ---------------------------------------------------------------- Tracker


class Tracker:
    """按键记录的调度中心。"""

    def __init__(self, config: Optional[Config] = None):
        self.config: Config = config or Config.load()
        self.db: Database = Database(self.config.db_path)
        self.db.initialize()

        self._queue: "queue.Queue[EventRecord]" = queue.Queue(maxsize=50000)
        self._clip_queue: "queue.Queue[ClipRecord]" = queue.Queue(maxsize=5000)
        self._hook: Optional[winapi.KeyboardHook] = None
        self._clip_watcher: Optional[clipboard.ClipboardWatcher] = None
        self._clip_error: str = ""
        self._key_observer: Optional[Callable[[KeyPreview], None]] = None
        self._clip_copy: int = 0
        self._clip_paste: int = 0
        self._clip_masked: int = 0
        self._clip_dropped: int = 0
        self._last_clip: Optional[Tuple[float, str, int, str]] = None  # (时间, 类型, 字符数, 正文)

        self._lock: Any = threading.RLock()
        self._stop: threading.Event = threading.Event()

        # 计数：内存里的当日累计 + 队列中尚未落库的部分
        self._day: str = day_str()
        self._counters: Dict[str, Dict[str, int]] = {}
        self._hourly: Dict[int, int] = {}
        self._minute_window: List[float] = []
        self._session_keys: int = 0
        self._masked_keys: int = 0
        self._dropped: int = 0

        self._session_id: Optional[int] = None
        self._session_started: Optional[float] = None
        self._last_event_ts: Optional[float] = None
        self._paused: bool = bool(self.config.start_paused)
        self._paused_reason: str = "启动时暂停" if self._paused else ""
        self._idle_paused: bool = False
        self._pending_session_reason: str = ""
        self._masking_now: bool = False
        self._remote: bool = False
        self._remote_note: str = ""
        self._current_window: Optional[winapi.WindowInfo] = None
        self._window_checked_at: float = 0.0
        self._last_title: str = ""

        self._writer: Optional[threading.Thread] = None
        self._maintainer: Optional[threading.Thread] = None
        self._listeners: List[Callable[[], None]] = []
        # 实时通道：每敲一下就通知一次，供界面把键盘热力图/计数尽快画出来。
        # 与 _listeners 分开，是因为这里的回调会被高频触发，界面只做轻量刷新。
        self._live_listeners: List[Callable[[], None]] = []

    # -- 生命周期 -------------------------------------------------

    def start(self) -> bool:
        self._stop.clear()
        self._writer = threading.Thread(target=self._write_loop, name="keylens-writer", daemon=True)
        self._writer.start()
        self._maintainer = threading.Thread(target=self._maintain_loop, name="keylens-maintain", daemon=True)
        self._maintainer.start()

        self._open_session(reason="启动")
        self._hook = winapi.KeyboardHook(self.on_key)
        ok = self._hook.start()
        if not ok:
            self._paused = True
            self._paused_reason = self._hook.error or "键盘钩子安装失败"
        self._start_clipboard()
        return ok

    def _start_clipboard(self) -> bool:
        """启动剪贴板监听。失败不影响键盘记录，只在界面上给一句提示。"""
        cfg = self.config.clipboard
        if not cfg.enabled or not (cfg.track_copy or cfg.track_paste):
            return False
        if not cfg.track_copy:
            # 只记粘贴时不需要监听剪贴板，省掉一个线程。
            self._clip_error = ""
            return False
        peek = cfg.max_text_chars if cfg.store_text else 0
        save_dir = ""
        if cfg.save_images:
            save_dir = clipboard.image_save_dir()
            self._cleanup_clip_images(save_dir)
        watcher = clipboard.ClipboardWatcher(
            self.on_clipboard_change, peek_chars=peek, save_dir=save_dir)
        if not watcher.start():
            self._clip_error = watcher.error or "剪贴板监听启动失败"
            return False
        self._clip_watcher = watcher
        self._clip_error = ""
        return True

    @staticmethod
    def _cleanup_clip_images(save_dir: str) -> None:
        """删除超过 3 天的剪贴板图片临时文件（.bmp）。"""
        try:
            cutoff = time.time() - 3 * 86400
            for name in os.listdir(save_dir):
                path = os.path.join(save_dir, name)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass

    def stop(self) -> None:
        if self._clip_watcher:
            self._clip_watcher.stop()
            self._clip_watcher = None
        if self._hook:
            self._hook.stop()
            self._hook = None
        self._idle_paused = False
        self._paused = True
        self._paused_reason = "已退出"
        self._stop.set()
        # 让维护线程立刻感知退出
        if self._maintainer and self._maintainer.is_alive():
            self._maintainer.join(timeout=3.0)
        self._flush(force=True)
        self._close_session()
        if self._writer and self._writer.is_alive():
            self._writer.join(timeout=3.0)
        try:
            self.db.checkpoint()
        except Exception:
            pass

    # -- 对外控制 -------------------------------------------------

    def pause(self, reason: str = "手动暂停") -> None:
        with self._lock:
            self._paused = True
            self._paused_reason = reason
        self._notify()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._paused_reason = ""
            self._idle_paused = False
            self._last_event_ts = None
        self._notify()

    def toggle_pause(self) -> None:
        if self._paused:
            self.resume()
        else:
            self.pause()

    def clear_records(self) -> None:
        """清空全部本地记录，并同步归零内存累计与当前会话。

        给 GUI 的"清除记录"用：删掉本机所有按键 / 剪贴板数据，同时把界面上
        仍显示的内存累计一并清零，否则清库后指标卡还会挂着旧数字。
        这是破坏性操作，调用方应先和用户二次确认。
        """
        was_paused = self._paused
        self.pause("清除记录")
        try:
            self.db.purge_all()
            self.db.checkpoint()
        finally:
            with self._lock:
                self._counters = {}
                self._hourly = {}
                self._minute_window = []
                self._session_keys = 0
                self._masked_keys = 0
                self._clip_copy = 0
                self._clip_paste = 0
                self._clip_masked = 0
                self._remote = False
                self._remote_note = ""
            # 重新开一个干净会话，避免后续事件引用已被删掉的旧 session_id
            self._open_session("记录已清除")
            if not was_paused:
                self.resume()
            self._notify()

    def set_session_label(self, label: str) -> None:
        """给当前会话打备注，例如"向日葵远程协助 14:20"。"""
        with self._lock:
            session_id = self._session_id
        if session_id is not None:
            self.db.update_session(session_id, label=label)
        self._notify()

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def add_live_listener(self, callback: Callable[[], None]) -> None:
        """注册"按键级"实时回调（每个按键、每次复制/粘贴都会触发一次）。

        回调同样在钩子线程里被调用，只用来置个标记；
        界面应自行节流（例如合并成 10Hz 刷新），不要在这里做重活。
        """
        self._live_listeners.append(callback)

    def _notify_live(self) -> None:
        for callback in list(self._live_listeners):
            try:
                callback()
            except Exception:  # 实时通知出问题绝不能影响记录
                pass

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                pass

    # -- 钩子回调（必须极快返回） ---------------------------------

    def on_key(self, event: winapi.KeyEvent) -> None:
        if self._paused:
            return
        now = event.ts

        # 会话轮换只在这里"打个标记"，真正的建会话（含磁盘写入）交给维护线程，
        # 保证钩子回调永远不做 IO，不会拖慢系统输入。
        if self._idle_paused:
            self._idle_paused = False
            self._pending_session_reason = "空闲后恢复"
        if self._last_event_ts and (now - self._last_event_ts) > self.config.session_gap_seconds:
            self._pending_session_reason = "间隔过长"
        self._last_event_ts = now

        window = self._window()
        masked = self._should_mask(window)
        key_id = "MASKED" if masked else event.key

        record = EventRecord(
            ts=now,
            session_id=self._session_id or 0,
            key_id=key_id,
            category=keymap.category(key_id),
            vk=event.vk,
            scan=event.scan,
            flags=(1 if event.extended else 0) | (16 if event.injected else 0),
            process=(window.process if window else "") or "",
            title=(self._title_for(window) if self.config.store_window_titles else ""),
        )
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._dropped += 1
            return
        self._bump_counters(record)
        self._maybe_paste(event, window, masked)
        self._notify_observer(event, masked, now)
        self._notify_live()

    # -- 外部：按键浮层（实时显示，不落库） ------------------------

    def set_key_observer(self, callback: Optional[Callable[[KeyPreview], None]]) -> None:
        """注册实时按键回调，只用来驱动按键浮层显示。

        回调会在**钩子线程**里被调用，所以里面只能做入队这种不阻塞的事；
        它拿到的数据不写数据库、不进统计，传 ``None`` 注销。
        """
        self._key_observer = callback

    def _notify_observer(self, event: winapi.KeyEvent, masked: bool, now: float) -> None:
        observer = self._key_observer
        if observer is None:
            return
        try:
            ctrl, shift, alt = winapi.modifiers_down()
            observer(
                KeyPreview(
                    key=event.key,
                    masked=masked,
                    injected=event.injected,
                    ctrl=ctrl,
                    shift=shift,
                    alt=alt,
                    ts=now,
                )
            )
        except Exception:  # 浮层的事绝不能影响记录
            pass

    # -- 内部：复制 / 粘贴 ----------------------------------------

    def on_clipboard_change(self, snapshot: clipboard.ClipSnapshot) -> None:
        """剪贴板内容变化（复制 / 剪切 / 程序写入剪贴板）。"""
        cfg = self.config.clipboard
        if self._paused or not cfg.enabled or not cfg.track_copy:
            return
        window = self._window()
        masked = self._should_mask_clipboard(window)  # 剪贴板用更宽松的判定，普通复制不被误伤
        if masked:
            # 掩码场景：只登记"发生过一次复制"，类型、长度、正文一律不取。
            content, formats, text_len, byte_len, note = "MASKED", "", 0, 0, ""
        else:
            content = snapshot.content
            formats = snapshot.formats
            text_len = snapshot.text_len
            byte_len = snapshot.byte_len
            note = snapshot.preview[: cfg.max_text_chars] if cfg.store_text else ""
            if snapshot.saved_path:
                # 复制的是图片：note 存临时 .bmp 路径，界面里可点击预览/放回剪贴板
                note = snapshot.saved_path
        with self._lock:
            self._last_clip = (snapshot.ts, content, text_len, note)
        self._enqueue_clip(
            ClipRecord(
                ts=snapshot.ts,
                session_id=self._session_id or 0,
                kind="copy",
                content=content,
                formats=formats,
                text_len=text_len,
                byte_len=byte_len,
                injected=0,
                process=(window.process if window else "") or "",
                title=(self._title_for(window) if self.config.store_window_titles else ""),
                masked=1 if masked else 0,
                note=note,
            )
        )
        self._notify_live()

    def _maybe_paste(self, event: winapi.KeyEvent, window: Optional[winapi.WindowInfo],
                     masked: bool) -> None:
        """识别 Ctrl+V / Ctrl+Shift+V / Shift+Insert，登记一次粘贴。

        粘贴的内容不重新读剪贴板（钩子回调里不能做这种可能阻塞的 IO），
        而是沿用最近一次剪贴板快照的类型与字符数。
        """
        cfg = self.config.clipboard
        if not cfg.enabled or not cfg.track_paste:
            return
        if event.key not in clipboard.PASTE_KEYS:
            return
        ctrl, shift, _alt = winapi.modifiers_down()
        if not clipboard.is_paste_combo(event.key, ctrl, shift):
            return

        content, text_len, note = "unknown", 0, ""
        if not masked:
            with self._lock:
                last = self._last_clip
            if last and (event.ts - last[0]) <= 300.0:
                content, text_len, note = last[1], last[2], last[3]
        self._enqueue_clip(
            ClipRecord(
                ts=event.ts,
                session_id=self._session_id or 0,
                kind="paste",
                content="MASKED" if masked else content,
                formats="",
                text_len=0 if masked else text_len,
                byte_len=0,
                injected=1 if event.injected else 0,
                process=(window.process if window else "") or "",
                title=(self._title_for(window) if self.config.store_window_titles else ""),
                masked=1 if masked else 0,
                note=note,
            )
        )

    def _enqueue_clip(self, record: ClipRecord) -> None:
        try:
            self._clip_queue.put_nowait(record)
        except queue.Full:
            with self._lock:
                self._clip_dropped += 1
            return
        with self._lock:
            if record.kind == "paste":
                self._clip_paste += 1
            else:
                self._clip_copy += 1
            if record.masked:
                self._clip_masked += 1

    # -- 内部：窗口与掩码 -----------------------------------------

    def _window(self) -> Optional[winapi.WindowInfo]:
        """前台窗口，带 0.3 秒缓存，避免每次按键都开进程句柄。"""
        now = time.time()
        if self._current_window is not None and (now - self._window_checked_at) < 0.3:
            return self._current_window
        info = winapi.foreground_window()
        self._current_window = info
        self._window_checked_at = now
        return info

    def _title_for(self, window: Optional[winapi.WindowInfo]) -> str:
        if window is None:
            return self._last_title
        self._last_title = window.title[:200]
        return self._last_title

    def _should_mask_clipboard(self, window: Optional[winapi.WindowInfo]) -> bool:
        """剪贴板专用的掩码判定：比按键更严格（也就是更少命中）。

        按键必须拦浏览器等密码相关进程；但复制动作发生在"用户主动复制"的时刻，
        如果只因为前台是浏览器就把内容也拦掉，正常网页复制全都变成"（掩码）"，
        反而失去了可用性。所以这里只在下面两种"确实在处理凭据"的情况下才拦：
        1) 窗口标题里出现密码/口令等关键字（如"输入密码"、"登录"）；
        2) 前台进程本身是密码管理器、解压/加密容器这类专用程序。
        浏览器 / 普通程序复制内容照常记录与显示。
        """
        privacy = self.config.privacy
        if not privacy.enabled or window is None:
            return False
        name = (window.process or "").lower()
        if name and not privacy.strict_archive_mask and name in ARCHIVE_APPS:
            # 压缩 / 加密容器默认放行：里面复制的内容也照常记录与显示
            # （连"输入解压密码"这类标题关键字也不拦）
            return False
        if window.title:
            low = window.title.lower()
            for kw in privacy.title_keywords:
                if kw and kw.lower() in low:
                    return True
        if not name:
            return False
        if privacy.clipboard_mask_strict:
            return name in privacy.password_apps or name in privacy.mask_processes
        if name in privacy.mask_processes:
            return True
        # 密码管理器仍然拦；浏览器不在此列——网页复制不该被连坐
        return name in privacy.password_apps and name not in _BROWSER_PROCESSES

    def _should_mask(self, window: Optional[winapi.WindowInfo]) -> bool:
        privacy = self.config.privacy
        masking = False
        if privacy.enabled and window is not None:
            name = (window.process or "").lower()
            if name and not privacy.strict_archive_mask and name in ARCHIVE_APPS:
                # 压缩 / 加密容器默认整段不掩码：用户要能看到自己在这里输入的口令。
                # 连"输入解压密码"这种标题关键字也不拦，否则等于没放开。
                masking = False
            elif name and (name in privacy.password_apps or name in privacy.mask_processes):
                masking = True
            elif window.title:
                low = window.title.lower()
                for kw in privacy.title_keywords:
                    if kw and kw.lower() in low:
                        masking = True
                        break
        with self._lock:
            self._masking_now = masking
        return masking

    # -- 内部：计数 -----------------------------------------------

    def _bump_counters(self, record: EventRecord) -> None:
        day = day_str(record.ts)
        with self._lock:
            if day != self._day:
                self._day = day
                self._counters = {}
                self._hourly = {}
                self._minute_window = []
                self._session_keys = 0
                self._masked_keys = 0
                self._clip_copy = 0
                self._clip_paste = 0
                self._clip_masked = 0
            key_bucket = self._counters.setdefault(record.key_id, {"count": 0})
            key_bucket["count"] += 1
            proc_key = "proc:" + (record.process or "(未知)")
            proc_bucket = self._counters.setdefault(proc_key, {"count": 0})
            proc_bucket["count"] += 1
            hour = time.localtime(record.ts).tm_hour
            self._hourly[hour] = self._hourly.get(hour, 0) + 1
            self._minute_window.append(record.ts)
            self._session_keys += 1
            if record.key_id == "MASKED":
                self._masked_keys += 1

    def _current_top(self, limit: int = 10) -> List[tuple]:
        with self._lock:
            items = [
                (k, v["count"])
                for k, v in self._counters.items()
                if not k.startswith("proc:") and k != "MASKED" and k in keymap.DISPLAY
            ]
            items.sort(key=lambda kv: kv[1], reverse=True)
            return items[:limit]

    # -- 实时统计（界面线程调用，纯内存） -------------------------

    def live_stats(self) -> LiveStats:
        now = time.time()
        with self._lock:
            cutoff = now - 60.0
            self._minute_window = [t for t in self._minute_window if t >= cutoff]
            total = sum(
                v["count"] for k, v in self._counters.items() if not k.startswith("proc:")
            )
            top_keys = sorted(
                ((k, v["count"]) for k, v in self._counters.items()
                 if not k.startswith("proc:") and k != "MASKED"),
                key=lambda kv: kv[1],
                reverse=True,
            )[:10]
            top_procs = sorted(
                ((k[5:], v["count"]) for k, v in self._counters.items() if k.startswith("proc:")),
                key=lambda kv: kv[1],
                reverse=True,
            )[:8]
            stats = LiveStats(
                total_keys=total,
                session_keys=self._session_keys,
                masked_keys=self._masked_keys,
                keys_last_minute=len(self._minute_window),
                top_keys=[(keymap.display(k), n) for k, n in top_keys],
                top_processes=top_procs,
                session_id=self._session_id,
                session_started=self._session_started,
                remote=self._remote,
                remote_note=self._remote_note,
                paused=self._paused,
                paused_reason=self._paused_reason,
                masking_now=self._masking_now,
                clip_copy=self._clip_copy,
                clip_paste=self._clip_paste,
                clip_masked=self._clip_masked,
                clipboard_active=self._clip_watcher is not None,
                clipboard_error=self._clip_error,
                db_path=self.db.path,
            )
        stats.active_minutes = (
            max(0.0, (now - self._session_started) / 60.0) if self._session_started else 0.0
        )
        stats.db_size = self.db.db_size()
        return stats

    @property
    def dropped_events(self) -> int:
        return self._dropped

    @property
    def dropped_clip_events(self) -> int:
        with self._lock:
            return self._clip_dropped

    @property
    def pause_reason(self) -> str:
        with self._lock:
            return self._paused_reason

    @property
    def paused(self) -> bool:
        """当前是否处于暂停（浮层 / 界面提示用）。"""
        with self._lock:
            return self._paused

    def today(self) -> str:
        """当前统计所属日期（跨零点会自动切换）。"""
        with self._lock:
            return self._day

    def hourly_counts(self) -> List[int]:
        with self._lock:
            return [self._hourly.get(h, 0) for h in range(24)]

    def key_counts(self) -> Dict[str, int]:
        with self._lock:
            return {
                k: v["count"]
                for k, v in self._counters.items()
                if not k.startswith("proc:")
            }

    # -- 会话 -----------------------------------------------------

    def _open_session(self, reason: str) -> None:
        self._close_session()
        remote, note = winapi.detect_remote_session()
        session_id = self.db.open_session(
            remote=remote,
            remote_note=note,
            masked=self.config.privacy.enabled,
        )
        with self._lock:
            self._session_id = session_id
            self._session_started = time.time()
            self._session_keys = 0
            self._masked_keys = 0
            self._remote = remote
            self._remote_note = note
        self._notify()

    def _close_session(self) -> None:
        with self._lock:
            session_id = self._session_id
            keys = self._session_keys
            self._session_id = None
            self._session_started = None
        if session_id:
            self.db.close_session(session_id, keys)

    # -- 写入线程 -------------------------------------------------

    def _write_loop(self) -> None:
        pending: List[EventRecord] = []
        clips: List[ClipRecord] = []
        last_flush = time.time()
        while not self._stop.is_set() or not self._queue.empty() or pending or clips:
            timeout = max(0.05, self.config.batch_interval / 2.0)
            try:
                record = self._queue.get(timeout=timeout)
                pending.append(record)
            except queue.Empty:
                pass
            self._drain_clips(clips)
            due = (time.time() - last_flush) >= self.config.batch_interval
            if pending and (len(pending) >= self.config.batch_size or due):
                self._store(pending)
                pending = []
                last_flush = time.time()
            if clips and (len(clips) >= 50 or due):
                self._store_clips(clips)
                clips = []
                last_flush = time.time()
            if self._stop.is_set() and self._queue.empty() and pending:
                self._store(pending)
                pending = []
                last_flush = time.time()

    def _drain_clips(self, sink: List[ClipRecord]) -> None:
        while True:
            try:
                sink.append(self._clip_queue.get_nowait())
            except queue.Empty:
                return

    def _store_clips(self, records: List[ClipRecord]) -> None:
        if not records:
            return
        try:
            self.db.insert_clipboard(records)
        except Exception:
            try:
                self._spill_clips(records)
            except Exception:
                pass

    def _spill_clips(self, records: List[ClipRecord]) -> None:
        path = os.path.join(self.config.data_dir, "spill-clipboard.jsonl")
        os.makedirs(self.config.data_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec.__dict__, ensure_ascii=False) + "\n")

    def _store(self, records: List[EventRecord]) -> None:
        try:
            self.db.insert_events(records)
        except Exception:
            # 落库失败时把数据丢进临时文件，避免静默丢失。
            try:
                self._spill(records)
            except Exception:
                pass

    def _spill(self, records: List[EventRecord]) -> None:
        path = os.path.join(self.config.data_dir, "spill.jsonl")
        os.makedirs(self.config.data_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec.__dict__, ensure_ascii=False) + "\n")

    def _flush(self, force: bool = False) -> None:
        pending: List[EventRecord] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if pending:
            self._store(pending)
        clips: List[ClipRecord] = []
        self._drain_clips(clips)
        if clips:
            self._store_clips(clips)

    # -- 维护线程 -------------------------------------------------

    def _maintain_loop(self) -> None:
        last_remote_poll = 0.0
        last_purge = 0.0
        while not self._stop.is_set():
            self._stop.wait(1.0)
            if self._stop.is_set():
                break
            now = time.time()

            # 1) 会话轮换：钩子线程只留标记，实际建会话在这里做（有 IO）
            reason = self._pending_session_reason
            if reason:
                self._pending_session_reason = ""
                try:
                    self._open_session(reason)
                except Exception:
                    pass

            # 2) 空闲自动暂停 / 恢复
            idle = winapi.idle_seconds()
            with self._lock:
                paused = self._paused
            if not paused:
                if idle >= self.config.pause_when_idle_seconds and not self._idle_paused:
                    self._idle_paused = True
                    # 空闲期间本就没有事件，这里只是标记状态。
                elif idle < 1.0 and self._idle_paused:
                    self._idle_paused = False

            # 3) 远程会话状态：从"本地"变为"远程"时切一个新会话
            if (now - last_remote_poll) >= self.config.remote_poll_seconds:
                last_remote_poll = now
                try:
                    remote, note = winapi.detect_remote_session()
                except Exception:
                    remote, note = False, ""
                with self._lock:
                    changed = remote != self._remote
                    session_id = self._session_id
                    self._remote = remote
                    self._remote_note = note
                if session_id:
                    self.db.update_session(session_id, remote=1 if remote else 0, remote_note=note)
                if changed:
                    self._open_session("远程会话开始" if remote else "远程会话结束")

            # 4) 保留期清理：每小时检查一次
            if (now - last_purge) > 3600:
                last_purge = now
                try:
                    self._enforce_retention()
                except Exception:
                    pass
        self._flush(force=True)

    def _enforce_retention(self) -> None:
        days = int(self.config.retention_days or 0)
        if days <= 0:
            return
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        self.db.purge_before(cutoff)
        self.db.checkpoint()


__all__ = [
    "Config",
    "PrivacyConfig",
    "ClipboardConfig",
    "KeyPreview",
    "Tracker",
    "LiveStats",
    "apply_mapping",
    "merge_mask_lists",
]
