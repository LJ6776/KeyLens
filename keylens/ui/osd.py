"""按键可视化浮层（OSD）：把刚按下的键以短动画的形式显示在屏幕角落。

安全边界（改动这个文件时请一并维护）：
  * 只在**内存**里显示最近几次按键，不落库、不写文件、不进任何统计；
  * 显示的是"按了哪个键"（键位标识，如 ``Ctrl + S``），不是"输入了什么字符"；
  * 命中隐私掩码、或焦点落在密码输入框时，立刻清空并停止显示；
  * 默认关闭，需要手动打开。开着它输入口令，口令依然会显示在屏幕上——
    这一步靠技术兜不住，只能靠使用的人自己注意。
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
from typing import Any, Callable, List, Optional, Tuple

from ..core import winapi
from . import theme as palette

LIFETIME = 1.6  # 单个按键在浮层上停留的秒数
MAX_KEYS = 8  # 同时显示的按键上限
# 浮层宽度：按内容实际宽度动态伸缩，最小 _MIN_OVERLAY_W、最大 _MAX_OVERLAY_W（屏幕一半，封顶 620px），
# 超出上限就在内部横向滚动
_MAX_OVERLAY_W = 620
_MIN_OVERLAY_W = 150
# "只显示按键"模式用这个颜色做透明色键：Windows 下 -transparentcolor 会把它变全透明，
# 于是只剩按键本身，没有面板底色
_MAGIC = "#FF00FF"
# 按停留时长分档换色，做出淡出的感觉
_FADE_STEPS = (0.6, 1.4)


def _fade_style(age: float) -> tuple[str, str]:
    """按键块按"距今多久"取 (底色, 字色)。

    分三档：新按下的最醒目（蓝底白字），中间档灰蓝底，快消失时深底暗字。
    底色和字色一起变——只改字色的话浮层太小，"刚按的"和"1 秒前按的"看着一样。

    写成函数而不是导入时算好的常量：主题可运行时切换，
    模块级取一次颜色的话，切到明亮主题后这几档还是暗色的值。
    """
    if age < _FADE_STEPS[0]:
        return palette.ACCENT_DIM, "#ffffff"  # 固定白字：ACCENT_DIM 在两种主题下都是深蓝
    if age < _FADE_STEPS[1]:
        return palette.BG_SOFT, palette.TEXT
    return palette.BG, palette.TEXT_DIM

_MASK_REASON = "命中隐私掩码，已停止显示按键"
_PASSWORD_REASON = "检测到密码输入框，已停止显示按键"

# 按键块的排版：字号 / 内边距 / 按键行高度。
# 浮层是贴边的小挂件，按键块要够醒目才看得出"刚按了什么"；
# 这几个值一起放大，画布高度必须同步加，否则按键会被画布裁掉上下沿。
_KEY_PT = 16
_KEY_PADX = 11
_KEY_PADY = 4
_KEY_ROW_H = 38


class KeyOverlay(tk.Toplevel):
    """不抢焦点、可拖动、始终置顶的小浮层。"""

    def __init__(
        self,
        master: tk.Misc,
        font_family: str = "Microsoft YaHei UI",
        on_close: Optional[Callable[[], None]] = None,
        config: Any = None,
    ) -> None:
        super().__init__(master)
        self._font_family = font_family
        self._on_close = on_close
        self.config = config  # 用来判断压缩/加密容器是否可以放行显示
        self._queue: "queue.Queue[object]" = queue.Queue(maxsize=256)
        self._shown: List[Tuple[float, str]] = []  # (时间戳, 显示文本)
        self._labels: List[tk.Label] = []
        self._hidden_reason = ""
        self._remote_label = ""
        self._prev_remote = ""
        self._remote_checked = 0.0
        self._paused = False
        self._drag: Optional[Tuple[int, int]] = None
        self._scroll_job: Optional[int] = None
        self._keys_only = False  # 只显示按键（背景透明、不显示面板）
        self.visible = False

        self.overrideredirect(True)
        self.configure(background=palette.BG_PANEL)
        self.resizable(False, False)
        try:
            self.attributes("-topmost", True)
            self.attributes("-alpha", 0.94)
        except Exception:
            pass

        wrap = tk.Frame(self, background=palette.BG_PANEL, padx=10, pady=6)
        wrap.pack(fill="both")
        self.wrap = wrap

        head = tk.Frame(wrap, background=palette.BG_PANEL)
        head.pack(fill="x")
        self._head = head
        tk.Label(
            head, text="KeyLens 按键浮层", background=palette.BG_PANEL,
            foreground=palette.TEXT_DIM, font=(font_family, 8),
        ).pack(side="left")
        close = tk.Label(
            head, text="×", background=palette.BG_PANEL, foreground=palette.TEXT_DIM,
            font=(font_family, 11), cursor="hand2",
        )
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())

        # 按键行走画布：内容超宽时内部横向滚动，不再把窗口撑出屏幕
        max_w = max(_MIN_OVERLAY_W, min(_MAX_OVERLAY_W, int(self.winfo_screenwidth() * 0.5)))
        self._max_w = max_w
        self._canvas = tk.Canvas(
            wrap, width=max_w, height=_KEY_ROW_H, highlightthickness=0, bd=0,
            background=palette.BG_PANEL)
        self._canvas.pack(anchor="w", pady=(4, 2))
        self.row = tk.Frame(self._canvas, background=palette.BG_PANEL)
        self._canvas.create_window((0, 0), window=self.row, anchor="nw")
        self._canvas.bind("<MouseWheel>", self._on_wheel)

        self.hint = tk.Label(
            wrap, text="密码输入框与掩码窗口下自动隐藏", background=palette.BG_PANEL,
            foreground=palette.TEXT_DIM, font=(font_family, 8),
        )
        self.hint.pack(anchor="w")

        for widget in (self, wrap, head, self._canvas, self.row):
            widget.bind("<ButtonPress-1>", self._press)
            widget.bind("<B1-Motion>", self._drag_move)
        # 只显示按键模式下没有标题栏，右键浮层即可关闭
        self.row.bind("<Button-3>", lambda _e: self.close())
        self._render()

    # -- 显示模式 -------------------------------------------------

    def set_keys_only(self, flag: bool) -> None:
        """切换"只显示按键"模式：背景透明、不显示标题与说明行，只留按键本身。"""
        flag = bool(flag)
        if flag == self._keys_only:
            return
        self._keys_only = flag
        self._apply_mode()
        if self.visible:
            self._render()

# -- 生命周期 -------------------------------------------------

    def _password_field(self) -> bool:
        """焦点是不是密码输入框。

        压缩 / 加密容器（WinRAR、7-Zip…）默认不在掩码名单里，它们里面的口令
        也要能显示，所以这里的"密码框"判定对它们同样放行；
        privacy.strict_archive_mask = true 时才恢复拦截。
        """
        if not winapi.focus_is_password_field():
            return False
        try:
            from ..core.tracker import ARCHIVE_APPS
            config = getattr(self, "config", None)
            privacy = getattr(config, "privacy", None)
            if privacy is not None and not privacy.strict_archive_mask:
                win = winapi.foreground_window()
                name = (win.process or "").lower() if win is not None else ""
                if name and name in ARCHIVE_APPS:
                    return False
        except Exception:
            pass
        return True

    def _apply_mode(self) -> None:
        """按当前模式切换配色、透明色与标题/说明行的显示。"""
        bg = _MAGIC if self._keys_only else palette.BG_PANEL
        for widget in (self, self.wrap, self._head, self.row, self._canvas):
            try:
                widget.configure(background=bg)
            except Exception:
                pass
        try:
            self.attributes("-transparentcolor", _MAGIC if self._keys_only else "")
        except Exception:  # 非 Windows 或属性不可用时忽略
            pass
        try:
            if self._keys_only:
                self._head.pack_forget()
                self.hint.pack_forget()
            else:
                if not self._head.winfo_ismapped():
                    self._head.pack(fill="x")
                if not self.hint.winfo_ismapped():
                    self.hint.pack(anchor="w")
        except Exception:
            pass

    def _sync_canvas(self, show: bool) -> None:
        """只显示按键模式下，没有按键时连画布一起隐藏，屏幕上什么都不留。"""
        try:
            if show and not self._canvas.winfo_ismapped():
                self._canvas.pack(anchor="w", pady=(4, 2))
            elif not show and self._canvas.winfo_ismapped():
                self._canvas.pack_forget()
        except Exception:
            pass

    def _fit_width(self) -> None:
        """按当前按键内容宽度伸缩浮层：不够宽就变宽，到上限就固定并内部滚动。"""
        try:
            # 先让 Tk 把刚 pack 的按键算完，否则读到的是上一帧的 reqwidth
            self.update_idletasks()
            need = self.row.winfo_reqwidth() + 8
            width = max(_MIN_OVERLAY_W, min(self._max_w, need))
            self._canvas.configure(width=width)
        except Exception:
            pass

    # -- 生命周期 -------------------------------------------------

    def show(self) -> None:
        self.visible = True
        self._shown = []
        self._hidden_reason = ""
        self._remote_label = ""
        self._prev_remote = ""
        self._render()
        self.deiconify()
        self.place_bottom_right()

    def hide(self) -> None:
        self.visible = False
        self._shown = []
        self.withdraw()

    def close(self) -> None:
        self.hide()
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass

    def place_bottom_right(self) -> None:
        self.update_idletasks()
        width = self.winfo_width() or 320
        height = self.winfo_height() or 76
        x = max(0, self.winfo_screenwidth() - width - 24)
        y = max(0, self.winfo_screenheight() - height - 64)
        self.geometry("+%d+%d" % (x, y))

    # -- 数据 -----------------------------------------------------

    def push(self, preview) -> None:
        """由 tracker 在钩子线程调用：只入队，不碰任何 Tk 控件。"""
        try:
            self._queue.put_nowait(preview)
        except queue.Full:
            pass

    def set_paused(self, paused: bool) -> None:
        """同步暂停状态：暂停时浮层明确提示，而不是一直显示"按任意键…"。"""
        self._paused = bool(paused)

    def pump(self) -> None:
        """在主线程定时调用：把队列里的按键画出来，并剔除过期的。"""
        if not self.visible:
            # 隐藏时也要排空队列，免得下次打开涌出一批旧按键
            self._drain()
            return

        got = False
        while True:
            try:
                preview = self._queue.get_nowait()
            except queue.Empty:
                break
            got = True
            if getattr(preview, "masked", False):
                self._shown = []
                self._hidden_reason = _MASK_REASON
                continue
            self._shown.append((preview.ts, preview.label))
            self._hidden_reason = ""

        now = time.time()
        if now - self._remote_checked >= 1.0:
            self._remote_checked = now
            try:
                self._remote_label = winapi.current_remote_app()
            except Exception:
                self._remote_label = ""

        if self._hidden_reason == _PASSWORD_REASON:
            # 焦点离开密码框后自动恢复显示
            try:
                if not self._password_field():
                    self._hidden_reason = ""
            except Exception:
                pass
        elif got and not self._hidden_reason:
            # 只在真有新按键时查一次焦点控件，别每轮刷新都去问系统
            try:
                if self._password_field():
                    self._shown = []
                    self._hidden_reason = _PASSWORD_REASON
            except Exception:
                pass

        before = len(self._shown)
        self._shown = [(ts, label) for ts, label in self._shown if now - ts <= LIFETIME]
        self._shown = self._shown[-MAX_KEYS:]
        if got or before or self._shown or self._remote_label != self._prev_remote:
            self._prev_remote = self._remote_label
            self._render()

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # -- 绘制 -----------------------------------------------------

    def _render(self) -> None:
        # 记录布局前的几何：内容变宽时要保持右边缘不动、向左伸展
        old_x, old_w = self.winfo_x(), self.winfo_width()
        while self._labels:
            self._labels.pop().destroy()
        # 底部提示行：远程 / 监控软件标注优先于默认说明
        if self._remote_label:
            self.hint.configure(
                text="远程 / 监控软件输入中 · " + self._remote_label,
                foreground=palette.ACCENT,
            )
        else:
            self.hint.configure(
                text="密码输入框与掩码窗口下自动隐藏",
                foreground=palette.TEXT_DIM,
            )
        if self._paused:
            self._add_label("记录已暂停（按 F8 恢复）", palette.WARN_BG, palette.WARN_TEXT, 10, "normal")
        elif self._hidden_reason:
            self._add_label(self._hidden_reason, palette.WARN_BG, palette.WARN_TEXT, 10, "normal")
        elif not self._shown:
            self._add_label("按任意键…", palette.BG_PANEL, palette.TEXT_DIM, 10, "normal")
            # 只显示按键模式下，空闲时连画布一起藏起来，屏幕上不留任何痕迹
            self._sync_canvas(not self._keys_only or bool(self._hidden_reason))
            self._fit_width()
            self._after_layout(old_x, old_w)
            return
        else:
            now = time.time()
            for ts, label in self._shown:
                age = now - ts
                bg, fg = _fade_style(age)
                self._add_label(label, bg, fg, _KEY_PT, "bold",
                                padx=_KEY_PADX, pady=_KEY_PADY)
        self._sync_canvas(True)
        self._fit_width()
        self._after_layout(old_x, old_w)

    def _after_layout(self, old_x: int, old_w: int) -> None:
        """布局收尾：刷新滚动区、快速滚到最新键、内容变宽时向左伸展。"""
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except Exception:
            pass
        self._scroll_to_end()
        try:
            self.update_idletasks()
            new_w = self.winfo_width()
            if new_w and new_w != old_w:
                # 右边缘钉在原来的位置：新内容从左边长出，而不是向右探出屏幕
                right = old_x + old_w
                screen_w = self.winfo_screenwidth()
                new_x = min(max(0, right - new_w), max(0, screen_w - new_w))
                self.geometry("+%d+%d" % (new_x, self.winfo_y()))
        except Exception:
            pass

    def _on_wheel(self, event) -> None:
        """滚轮横向滚动按键行（内容超宽时）。"""
        try:
            self._canvas.xview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _scroll_to_end(self) -> None:
        """内容超出画布时，快速平滑滚到最右（最新按下的键）。"""
        try:
            bbox = self._canvas.bbox("all")
            if not bbox:
                return
            content_w = bbox[2] - bbox[0]
            view_w = self._canvas.winfo_width()
            if content_w <= view_w:
                return
            target = (content_w - view_w) / content_w
            if self._scroll_job is not None:
                self.after_cancel(self._scroll_job)
                self._scroll_job = None
            self._animate_scroll(target, 0, 5)
        except Exception:
            pass

    def _animate_scroll(self, target: float, step: int, steps: int) -> None:
        """约 5 帧、80ms 内 ease-out 滚到目标位置。"""
        try:
            start = self._canvas.xview()[0]
            if step >= steps or abs(target - start) < 0.002:
                self._canvas.xview_moveto(target)
                self._scroll_job = None
                return
            t = (step + 1) / steps
            eased = 1.0 - (1.0 - t) ** 2  # ease-out：先快后慢
            self._canvas.xview_moveto(start + (target - start) * eased)
            self._scroll_job = self.after(16, lambda: self._animate_scroll(target, step + 1, steps))
        except Exception:
            self._scroll_job = None

    def _add_label(self, text: str, bg: str, fg: str, size: int, weight: str,
                   padx: int = 8, pady: int = 3) -> None:
        label = tk.Label(
            self.row, text=text, background=bg, foreground=fg,
            font=(self._font_family, size, weight), padx=padx, pady=pady,
        )
        label.pack(side="left", padx=3)
        self._labels.append(label)

    # -- 拖动 -----------------------------------------------------

    def _press(self, event) -> None:
        self._drag = (event.x_root, event.y_root)

    def _drag_move(self, event) -> None:
        if not self._drag:
            return
        dx = event.x_root - self._drag[0]
        dy = event.y_root - self._drag[1]
        self._drag = (event.x_root, event.y_root)
        self.geometry("+%d+%d" % (self.winfo_x() + dx, self.winfo_y() + dy))


__all__ = ["KeyOverlay", "LIFETIME", "MAX_KEYS"]
