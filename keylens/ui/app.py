"""常驻可见的监控窗口：实时计数、键盘热力图、会话与隐私状态。

设计取向（刻意为之）：
  * 窗口**始终可见**，标题栏直接写明"按键记录器"，不做任何隐藏；
  * 暂停 / 退出按钮一直在窗口上，一键可停；
  * 热力图用真实键位布局，让"哪个键最费手"一眼可见；
  * 命中隐私掩码时，窗口上会亮起明确的橙色提示条。
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    # 直接以脚本方式运行（python app.py / python ui\app.py）时没有包上下文，
    # 相对导入会失败。把项目根目录塞进 sys.path，再以包方式重新执行主入口。
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import runpy

    _ = runpy.run_module("keylens.main", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import math
import re
import shutil
import subprocess
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any, Callable

from .. import __version__
from ..core import clipboard, db as db_module, keymap
from ..core.tray import DEFAULT_HOTKEY, TrayIcon, default_tooltip, parse_hotkey

if TYPE_CHECKING:
    from ..core.tracker import Config, Tracker
from . import theme as palette
from .heat import heat_color as _heat_color

# 剪贴板面板里"没取到快照"显示短一点，避免单行被撑爆
_CLIP_DISPLAY = {**clipboard.CONTENT_DISPLAY, "unknown": "来源未知"}

# 时间线「按程序筛选」下拉里的两个固定项
_TL_ALL = "全部程序"
_TL_UNKNOWN = "(未知)"
# 掩码项在界面上的明文说明（不用「（掩码）」这种符号化写法）
_MASK_TEXT = "隐私掩码 · 不记录内容"
# 日期下拉的"今天"标签（其余选项是具体日期字符串）
_DAY_TODAY = "今天"

# 按键级实时刷新的节流间隔（毫秒）：按键后最多 100ms 就把热力图画出来，
# 连打时合并成 10Hz，既跟手又不会把 Tk 主线程塞满。
_LIVE_REFRESH_MS = 100


def _pick_font(root: tk.Misc) -> str:
    """挑一个能同时显示中英文的字体。"""
    try:
        available = set(tkfont.families(root))
    except Exception:
        return "Segoe UI"
    for name in ("微软雅黑", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimHei"):
        if name in available:
            return name
    return "TkDefaultFont"


class HeatmapCanvas(tk.Canvas):
    """按真实键位布局绘制的热力图。

    键盘拆成若干"块"，每块自己算宽度（以字母键为 1 单位），
    再按画布大小缩放，缩放时重建图元；数值更新只做 itemconfig。
    """

    TITLE_H: float = 11.0

    def __init__(
        self, master: tk.Misc, blocks: Any, min_unit: float = 30.0, max_unit: float = 62.0,
        side_columns: Any = None, **kwargs: Any,
    ) -> None:
        super().__init__(master, highlightthickness=0, bd=0, background=palette.BG_PANEL, **kwargs)
        self.blocks: Any = blocks
        # 右侧并排的若干列；每项为 (键区列表, 起始跳过的左列键区数)，
        # 跳过数决定这一列顶部与左列哪个位置对齐（0 = 与功能键行同高）。
        self.side_columns: list[Any] = [(list(blocks_), int(skip)) for blocks_, skip in (side_columns or [])]
        self.min_unit: float = min_unit
        self.max_unit: float = max_unit
        self.unit: float = max_unit
        self._max: int = 0
        self._font_family: str = "Segoe UI"
        self._positions: list[tuple[str, float, float, float, float]] = []
        self._items: dict[str, tuple[int, int, int, str]] = {}
        # 每个键的键名锚点（中心 x、顶边 y、键高），用来在"有无次数"时挪键名
        self._anchors: dict[str, tuple[float, float, float]] = {}
        # 测量文字宽度用的字体对象（按 字体+字号 缓存）：重绘时每个键都要量一次，
        # 重复创建 Font 会明显拖慢窗口缩放。
        self._font_cache: dict[tuple[str, int], tkfont.Font] = {}
        self._signature: Any = None
        self.bind("<Configure>", self._on_configure)

    # -- 几何 -----------------------------------------------------

    PAD_RATIO: float = 0.06  # 键间隙 / 单位键宽，参考真实键盘测试页的留白感
    # 键高 / 单位键宽。键区是"高度受限"的：需要的高度约 5.3×unit + 22，
    # 每压一点这个比例，同样的画布高度就能装下更大的键（左右空白更少）。
    # 0.78 时键面接近笔记本键盘的观感，再扁就不像键盘了。
    KEY_H_RATIO: float = 0.78
    LABEL_MIN_PT: int = 5  # 键名最小字号：再小就看不清了
    LABEL_PAD: float = 6.0  # 键名左右留白（像素），避免文字贴住键框线
    # 键名与次数的纵向位置（相对键高的比例）：
    # 只有键名时居中；键名 + 次数两行时，键名在上、次数在下。
    LABEL_Y_SOLO: float = 0.5
    LABEL_Y_WITH_COUNT: float = 0.34
    COUNT_Y: float = 0.73

    def _pad(self, unit: float) -> float:
        """键间隙随键大小等比缩放，键越大留白越大。"""
        return unit * self.PAD_RATIO

    def _block_size(self, rows: list[Any], unit: float) -> tuple[float, int]:
        """一块键区的 (内容宽度, 行数)。

        行内元素：元组 = 键 (键名, 宽[, 跨行数])；None = 1 单位空位；
        浮点数 = 指定单位宽度的空隙。
        宽度按**键距**算：1 键距 = 键宽 + 键间隙，宽键按它实际占的键距数计入
        （2u 的键占 2 个键距，键面里含 1 个键间隙）。
        早期版本把宽键当成"键宽 + 1 个间隙"，导致宽键之后的键整体左移半个间隙，
        小键盘的 "." 就跟"小键盘3"错开了；按键距算之后各行右缘也能对齐。
        """
        pad = self._pad(unit)
        pitch = unit + pad
        width = 0.0
        for row in rows:
            row_w = sum(self._slot_width(item) for item in row) * pitch - pad
            width = max(width, row_w)
        return width, len(rows)

    def _slot_width(self, item: Any) -> float:
        """一个槽位占用的单位宽度。"""
        if isinstance(item, tuple):
            return item[1]
        if item is None:
            return 1.0
        return float(item)

    def _title_h(self, unit: float) -> float:
        """键区标题占位高度：随键宽缩放，小键盘时保底 TITLE_H。

        标题字号随 unit 变大（int(unit*0.18)），占位高度若固定会
        在大尺寸下被文字压到第一排按键，所以这里同步缩放。
        """
        return max(self.TITLE_H, unit * 0.30)

    def _measure_font(self, family: str, size: int) -> tkfont.Font:
        """取（可测量的）字体对象，按 字体+字号 缓存。"""
        key = (family, size)
        font = self._font_cache.get(key)
        if font is None:
            font = tkfont.Font(root=self, family=family, size=size)
            self._font_cache[key] = font
        return font

    def _fit_size(self, family: str, size: int, text: str, max_w: float) -> int:
        """把字号一路缩到文字放得下为止。"""
        while size > self.LABEL_MIN_PT:
            if self._measure_font(family, size).measure(text) <= max_w:
                break
            size -= 1
        return size

    def _fit_label(self, family: str, size: int, key: str, max_w: float) -> tuple[str, int]:
        """字号自适应：文字比键框还宽时逐级缩小，保证不压出键框。

        窗口没最大化时键很小，"小键盘7"这种 4 个字的标签会顶到键框线上，
        再被相邻键的边框盖住，看起来就像键边上长了一圈锯齿。
        缩到最小字号还放不下时改用紧凑名（数字键区只留数字）。
        """
        label = keymap.display(key)
        size = self._fit_size(family, size, label, max_w)
        if self._measure_font(family, size).measure(label) > max_w:
            short = keymap.display_compact(key)
            if short != label and self._measure_font(family, size).measure(short) <= max_w:
                return short, size
        return label, size

    def _column_size(self, blocks: Any, unit: float) -> tuple[float, float]:
        """一列键区的 (宽度, 高度)。"""
        key_h = unit * self.KEY_H_RATIO
        pad = self._pad(unit)
        width = 0.0
        height = pad
        for title, rows in blocks:
            content_w, nrows = self._block_size(rows, unit)
            width = max(width, content_w)
            height += (self._title_h(unit) if title else 0.0) + nrows * (key_h + pad) + pad
        return width + pad * 2, height + pad

    def _skip_y(self, skip: int, unit: float) -> float:
        """左列前 skip 个键区占用的纵向高度（侧列起始纵坐标）。"""
        key_h = unit * self.KEY_H_RATIO
        pad = self._pad(unit)
        y = pad
        for title, rows in self.blocks[: max(0, skip)]:
            if title:
                y += self._title_h(unit)
            y += len(rows) * (key_h + pad) + pad
        return y

    def _required(self, unit: float) -> tuple[float, float]:
        """给定单位宽度，算出画布需要的 (宽, 高)。"""
        left_w, left_h = self._column_size(self.blocks, unit)
        if not self.side_columns:
            return left_w, left_h
        gap = unit * 0.4  # 列间隔，接近真实键盘键区之间的距离
        width = left_w
        height = left_h
        for col_blocks, skip in self.side_columns:
            col_w, col_h = self._column_size(col_blocks, unit)
            width += gap + col_w
            height = max(height, self._skip_y(skip, unit) + col_h)
        return width, height

    def _on_configure(self, event: tk.Event) -> None:
        pad = self._pad(self.unit)
        available_w = max(80, event.width - 2 * pad)
        available_h = max(80, event.height - 2 * pad)
        unit = self.min_unit
        # 0.5px 一档细分搜索，让键盘尽量撑满画布宽度，
        # 减少左右居中后的空白（粗步长会留下明显边距）
        steps = int((self.max_unit - self.min_unit) / 0.5) + 1
        for step in range(steps):
            candidate = self.max_unit - step * 0.5
            need_w, need_h = self._required(candidate)
            if need_w <= available_w and need_h <= available_h:
                unit = candidate
                break
        signature = (round(unit, 2), event.width, event.height)
        if signature == self._signature:
            return
        self._signature = signature
        self.unit = unit
        self._rebuild()

    def _rounded_key(self, x1: float, y1: float, x2: float, y2: float) -> int:
        """圆角键帽：显式采样圆角、直边保持笔直。

        之前用 ``create_polygon(smooth=True)``，Tk 的样条会把**整条直边**也一起
        拉弯；键小的时候 1px 描边就成了一段一段的"锯齿"（窗口没最大化时最明显）。
        这里改成自己算圆弧采样点，用折线画（smooth=False），直边就是直的。
        """
        r = min(x2 - x1, y2 - y1) * 0.18
        if r < 1.0:
            r = 0.0
        pts: list[float] = []
        if r <= 0.0:
            pts = [x1, y1, x2, y1, x2, y2, x1, y2]
        else:
            steps = 4
            # 右上 -> 右下 -> 左下 -> 左上，每段 90° 圆弧
            for cx, cy, start in (
                (x2 - r, y1 + r, -90.0),
                (x2 - r, y2 - r, 0.0),
                (x1 + r, y2 - r, 90.0),
                (x1 + r, y1 + r, 180.0),
            ):
                for step in range(steps + 1):
                    angle = math.radians(start + 90.0 * step / steps)
                    pts.append(cx + r * math.cos(angle))
                    pts.append(cy + r * math.sin(angle))
        return self.create_polygon(
            *pts, fill=palette.KEY_IDLE, outline=palette.KEY_BORDER, width=1,
        )

    def _rebuild(self) -> None:
        self.delete("all")
        self._items.clear()
        self._positions.clear()
        self._anchors.clear()
        unit = self.unit
        pad = self._pad(unit)
        key_h = unit * self.KEY_H_RATIO
        label_size = max(7, int(unit * 0.20))
        count_size = max(7, int(unit * 0.23))
        title_size = max(7, int(unit * 0.18))

        def draw_blocks(blocks: Any, x0: float, y0: float) -> float:
            """在 (x0, y0) 起绘制一列键区，返回这一列的内容宽度。"""
            y = y0
            width = 0.0
            for title, rows in blocks:
                if title:
                    title_h = self._title_h(unit)
                    self.create_text(
                        x0, y + title_h * 0.5, text=title, anchor="w",
                        fill=palette.TEXT_DIM, font=(self._font_family, title_size),
                    )
                    y += title_h
                content_w, _nrows = self._block_size(rows, unit)
                width = max(width, content_w)
                for row in rows:
                    x = x0
                    for item in row:
                        if item is None:
                            # 被跨行键（+ / Enter）占掉的空位：正好一个键距
                            x += unit + pad
                            continue
                        if not isinstance(item, tuple):
                            # 指定宽度的空隙（如功能键行的分组间隔）
                            x += float(item) * (unit + pad)
                            continue
                        key, w = item[0], item[1]
                        span_h = item[2] if len(item) > 2 else 1
                        # 宽键把内部的键间隙也算进键面，键面才和键距一样宽
                        kw = w * unit + (w - 1) * pad
                        kh = span_h * key_h + (span_h - 1) * pad
                        rect = self._rounded_key(x, y, x + kw, y + kh)
                        label, label_pt = self._fit_label(
                            self._font_family, label_size, key, kw - self.LABEL_PAD)
                        # 刚建好时还没有次数，键名先居中；等有了次数再挪到上半部分
                        label_item = self.create_text(
                            x + kw / 2, y + kh * self.LABEL_Y_SOLO, text=label,
                            fill=palette.TEXT_DIM, font=(self._font_family, label_pt),
                        )
                        count_pt = self._fit_size(
                            self._font_family, count_size, "00000", kw - self.LABEL_PAD)
                        count = self.create_text(
                            x + kw / 2, y + kh * self.COUNT_Y, text="",
                            fill=palette.TEXT_BRIGHT, font=(self._font_family, count_pt, "bold"),
                        )
                        self._positions.append((key, x, y, kw, kh))
                        self._items[key] = (rect, label_item, count, "")
                        self._anchors[key] = (x + kw / 2, y, kh)
                        x += kw + pad
                    y += key_h + pad
                y += pad
            return width

        # 画布有富余时整体居中：按各列真实内容宽度（键面外缘）精确计算，
        # 避免键盘缩放后全部挤在左侧或右侧留白不对称
        gap = unit * 0.4
        left_c = max(self._block_size(rows, unit) for _t, rows in self.blocks)[0]
        side_cs = [
            max(self._block_size(rows, unit) for _t, rows in col)[0]
            for col, _skip in self.side_columns
        ]
        drawn_w = left_c + sum(gap + sc for sc in side_cs)
        total_h = self._required(unit)[1]
        canvas_w = self.winfo_width()
        canvas_h = self.winfo_height()
        x_start = max(pad, (canvas_w - drawn_w) / 2)
        y_start = max(pad, (canvas_h - total_h) / 2)

        draw_blocks(self.blocks, x_start, y_start)
        if self.side_columns:
            x = x_start + left_c + gap
            for (col_blocks, skip), col_c in zip(self.side_columns, side_cs):
                draw_blocks(col_blocks, x, y_start + self._skip_y(skip, unit) - pad)
                x += col_c + gap
        self._max = 1

    # -- 刷新 -----------------------------------------------------

    def set_font(self, family: str) -> None:
        if family != self._font_family:
            self._font_family = family
            self._signature = None

    def update_values(self, counts: dict[str, int]) -> None:
        if not self._positions:
            self._rebuild()
        maximum = 1
        for key, _x, _y, _kw, _kh in self._positions:
            value = counts.get(key, 0)
            if value > maximum:
                maximum = value
        # 颜色与次数必须一起刷：颜色 = f(该键次数 / 当日最大次数)，
        # 之前"最大值没变就只写次数"的捷径会让新按下的键只有数字没有颜色
        # （最大值封顶后，新键的首次按键永远等不到重着色）。
        self._max = maximum
        for key, _x, _y, _kw, _kh in self._positions:
            value = counts.get(key, 0)
            rect, _label, _count, _shown = self._items[key]
            self.itemconfig(rect, fill=_heat_color(value, maximum))
            self._set_count(key, value)

    def _set_count(self, key: str, value: int) -> None:
        """写入某个键的次数，并按"有没有次数"调整键名的纵向位置。

        只有键名时把键名放在键的正中；出现次数后改成"键名在上、次数在下"两行。
        否则键名一直贴着上边沿，下边空一大片，看着很别扭。
        """
        rect, label_item, count, shown = self._items[key]
        text = str(value) if value else ""
        if text == shown:
            return
        self.itemconfig(count, text=text)
        self._items[key] = (rect, label_item, count, text)
        center_x, top_y, key_h = self._anchors[key]
        ratio = self.LABEL_Y_WITH_COUNT if text else self.LABEL_Y_SOLO
        self.coords(label_item, center_x, top_y + key_h * ratio)


class _CheckRow(tk.Frame):
    """手搓的勾选行（复选框 + 文案），整行都能点。

    为什么不用 ``ttk.Checkbutton``：clam 主题的指示器是两张位图
    （未选=浅灰方块、选中=灰底白叉），``-indicatorcolor`` 只在位图缺失时
    才生效，改不动；跟卡片配色摆一起很扎眼。这里用 Canvas 画一个小方框，
    选中时是主色底 + 白色对勾，和界面其它地方一致。

    颜色直接读 palette：切换主题时设置窗口会被重建（见 ``_switch_mode``），
    所以不需要额外的换色逻辑。
    """

    BOX: int = 15  # 方框边长

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        on_change: Callable[[], None],
        font_family: str,
    ) -> None:
        super().__init__(master, background=palette.BG_PANEL, cursor="hand2")
        self._var: tk.BooleanVar = variable
        self._on_change: Callable[[], None] = on_change
        self._box: tk.Canvas = tk.Canvas(
            self, width=self.BOX, height=self.BOX, highlightthickness=0, bd=0,
            background=palette.BG_PANEL, cursor="hand2")
        self._box.pack(side="left", padx=(0, 9))
        self._label: tk.Label = tk.Label(
            self, text=text, background=palette.BG_PANEL, foreground=palette.TEXT,
            font=(font_family, 10), anchor="w", justify="left", cursor="hand2")
        self._label.pack(side="left", fill="x", expand=True)
        for widget in (self, self._box, self._label):
            widget.bind("<Button-1>", self._toggle)
        self.redraw()

    def _toggle(self, _event: Any = None) -> str:
        self._var.set(not self._var.get())
        self.redraw()
        if self._on_change is not None:
            self._on_change()
        return "break"

    def redraw(self) -> None:
        """重画方框：选中=主色底+白勾，未选=浅底描边。"""
        box = self._box
        box.delete("all")
        edge = self.BOX - 1
        if self._var.get():
            box.create_rectangle(1, 1, edge, edge, fill=palette.ACCENT,
                                 outline=palette.ACCENT)
            box.create_line(4, 7.5, 6.5, 10.5, fill="#ffffff", width=2, capstyle="round")
            box.create_line(6.5, 10.5, 11, 4.5, fill="#ffffff", width=2, capstyle="round")
        else:
            box.create_rectangle(1, 1, edge, edge, fill=palette.BG_SOFT,
                                 outline=palette.KEY_BORDER)


class KeyLensApp:
    """主窗口。"""

    # 底部"按键时间线 / 剪贴板记录"两块面板的最小高度（像素）：
    # 窗口不够高时先压缩键盘热力图，保证这两块始终能看到几行内容。
    TL_MIN_H: int = 200

    def __init__(self, tracker: "Tracker", config: "Config", report_module: Any = None, show_overlay: bool = False) -> None:
        self.tracker: "Tracker" = tracker
        self.config: "Config" = config
        self.report_module: Any = report_module
        self._last_summary_load: float = 0.0
        self._summary: Any = None
        self._closing: bool = False
        self.overlay: Any = None  # 按键浮层(KeyOverlay)，默认不开，点了按钮才创建
        self._tooltip: Any = None  # 悬浮提示弹窗
        self._hotkey_win: Any = None  # 设置快捷键弹窗（避免重复创建）
        self._overlay_ticking: bool = False
        self._live_job: Any = None  # 按键级实时刷新任务（节流后合并执行）
        # 按键时间线：按 id 增量读取（可选日期），避免每秒整表重绘
        self._tl_day: str | None = None
        self._tl_last_id: int = 0
        self._tl_max_rows: int = 2000
        self._tl_days: list[str] = []
        # 剪贴板记录：同样按 id 增量读取；支持按日期与前台程序筛选
        self._cl_day: str | None = None
        self._cl_last_id: int = 0
        self._cl_max_rows: int = 2000
        self._clip_procs: list[str] = []
        self._cl_days: list[str] = []
        self._clip_thumbs: list[object] = []  # 缩略图引用，防止 PhotoImage 被 GC
        # 时间线「按程序筛选」：默认全部程序，可切到当天某一前台进程
        # （tk.StringVar 必须在 root 创建之后再建，见下面）
        self._tl_procs: list[str] = []

        # 按配置应用明暗主题后再构建界面（全部配色运行时读 palette 常量）
        palette.set_mode(getattr(self.config, "ui_mode", "") or "dark")

        self.root: tk.Tk = tk.Tk()
        self.root.title("KeyLens 按键记录器")
        self.root.configure(background=palette.BG)
        self.root.minsize(960, 640)
        self.root.geometry("1200x780")
        self.font_family: str = _pick_font(self.root)
        # 必须在 root 创建之后再建，否则 tk 会报 "no default root window"
        self.tl_filter: tk.StringVar = tk.StringVar(master=self.root, value=_TL_ALL)
        self.clip_filter: tk.StringVar = tk.StringVar(master=self.root, value=_TL_ALL)
        self.tl_day: tk.StringVar = tk.StringVar(master=self.root, value=_DAY_TODAY)
        self.clip_day: tk.StringVar = tk.StringVar(master=self.root, value=_DAY_TODAY)
        self._build_style()
        self._build_layout()
        self._set_icon()
        self._setup_tray()

        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)
        self.root.bind("<Escape>", lambda _e: self.on_quit())
        self.root.bind("<F8>", lambda _e: self.toggle_pause())

        self.tracker.add_listener(self._schedule_refresh)
        # 键盘热力图等"按键级"刷新走实时通道：按键即刻触发，界面按 100ms 节流合并，
        # 这样按键后几乎立刻就能看到格子变化，而不需要等 1 秒的整窗刷新。
        self.tracker.add_live_listener(self._schedule_live_refresh)
        if show_overlay:
            self.root.after(200, self.toggle_overlay)
        self._tick()

    # -- 样式与骨架 -----------------------------------------------

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # 卡片：面板底色 + 1px 描边，形成清晰的分区层次
        style.configure("Card.TFrame", background=palette.BG_PANEL,
                        borderwidth=1, relief="solid", bordercolor=palette.BORDER)
        # 卡片内标题行：与卡片同底色但无边框（带边框会在空档处露出上下两条线）
        style.configure("Head.TFrame", background=palette.BG_PANEL, borderwidth=0)
        style.configure("Root.TFrame", background=palette.BG)
        style.configure("Title.TLabel", background=palette.BG, foreground=palette.TEXT_BRIGHT,
                        font=(self.font_family, 18, "bold"))
        style.configure("Subtitle.TLabel", background=palette.BG, foreground=palette.TEXT_DIM,
                        font=(self.font_family, 9))
        style.configure("Card.TLabel", background=palette.BG_PANEL, foreground=palette.TEXT,
                        font=(self.font_family, 9))
        style.configure("PanelTitle.TLabel", background=palette.BG_PANEL,
                        foreground=palette.TEXT_BRIGHT, font=(self.font_family, 10, "bold"))
        style.configure("Metric.TLabel", background=palette.BG_PANEL, foreground=palette.ACCENT,
                        font=(self.font_family, 24, "bold"))
        style.configure("Metric.Ok.TLabel", background=palette.BG_PANEL, foreground=palette.OK,
                        font=(self.font_family, 24, "bold"))
        style.configure("Metric.Warn.TLabel", background=palette.BG_PANEL, foreground=palette.WARN_TEXT,
                        font=(self.font_family, 24, "bold"))
        style.configure("Metric.Dim.TLabel", background=palette.BG_PANEL, foreground=palette.TEXT_BRIGHT,
                        font=(self.font_family, 24, "bold"))
        style.configure("MetricCap.TLabel", background=palette.BG_PANEL, foreground=palette.TEXT_DIM,
                        font=(self.font_family, 9))
        # 卡片内的说明文字：底色必须是 BG_PANEL。Status.TLabel 的底色是窗口底色，
        # 放进卡片里会露出一条灰带（明亮主题下尤其明显）。
        style.configure("Note.TLabel", background=palette.BG_PANEL, foreground=palette.TEXT,
                        font=(self.font_family, 9))
        style.configure("Version.TLabel", background=palette.BG_PANEL, foreground=palette.TEXT_DIM,
                        font=(self.font_family, 9))
        style.configure("Status.TLabel", background=palette.BG, foreground=palette.TEXT,
                        font=(self.font_family, 10))
        # 按钮：深色扁平；主操作蓝底、危险操作暖底
        style.configure("TButton", font=(self.font_family, 9),
                        background=palette.BG_SOFT, foreground=palette.TEXT,
                        borderwidth=0, padding=(14, 7))
        style.map("TButton",
                  background=[("pressed", palette.ACCENT_DIM), ("active", palette.KEY_BORDER)],
                  foreground=[("active", palette.TEXT_BRIGHT)])
        # 主色按钮底色两种主题都是深蓝，文字固定白色才够醒目
        # （TEXT_BRIGHT 在亮色模式下是深色，不能用作深底按钮文字）
        style.configure("Primary.TButton", background=palette.ACCENT_DIM, foreground="#ffffff")
        style.map("Primary.TButton",
                  background=[("pressed", palette.ACCENT), ("active", palette.ACCENT)],
                  foreground=[("pressed", "#ffffff"), ("active", "#ffffff")])
        style.configure("Danger.TButton", background=palette.WARN_BG, foreground=palette.WARN_TEXT)
        style.map("Danger.TButton",
                  background=[("pressed", palette.ALERT), ("active", "#5a3d0a")],
                  foreground=[("active", palette.WARN_TEXT)])
        # 图标按钮：无边框扁平样式，只显示 MDL2 字形，与标题栏融为一体；
        # 颜色区分语义：常规灰 / 主操作蓝 / 危险琥珀，悬停提亮，无底框。
        # width=3：MDL2 私有区字形被 Tk 测量得很宽（约 50px），不指定宽度
        # 按钮会撑到 50px+ 导致间距过大；固定小宽度 + 字形居中即可紧凑显示
        for name, fg, hover in (
            ("Icon.TButton", palette.TEXT_DIM, palette.TEXT_BRIGHT),
            ("Icon.Primary.TButton", palette.ACCENT, palette.TEXT_BRIGHT),
            ("Icon.Danger.TButton", palette.WARN_TEXT, palette.ALERT),
        ):
            style.configure(name, font=("Segoe MDL2 Assets", 12),
                            background=palette.BG, foreground=fg,
                            borderwidth=0, relief="flat", padding=(5, 4))
            style.map(name,
                      background=[("active", palette.BG), ("pressed", palette.BG)],
                      foreground=[("active", hover), ("pressed", hover)])
        style.configure("Filter.TCombobox",
                        fieldbackground=palette.BG_SOFT,
                        background=palette.BG_PANEL,
                        foreground=palette.TEXT,
                        selectbackground=palette.BG_SOFT,
                        selectforeground=palette.TEXT,
                        arrowcolor=palette.TEXT,
                        bordercolor=palette.KEY_BORDER,
                        lightcolor=palette.BG_PANEL,
                        darkcolor=palette.BG_PANEL,
                        font=(self.font_family, 9))
        # 关键：clam 主题下 readonly 状态走 map 分支，不映射的话
        # 输入框和箭头按钮会退回系统默认的亮色，在深色主题里非常刺眼
        style.map("Filter.TCombobox",
                  fieldbackground=[
                      ("readonly", palette.BG_SOFT),
                      ("disabled", palette.BG_PANEL),
                      ("active", palette.BG_SOFT)],
                  background=[
                      ("readonly", palette.BG_SOFT),
                      ("pressed", palette.BG_SOFT),
                      ("active", palette.BG_SOFT)],
                  foreground=[
                      ("readonly", palette.TEXT),
                      ("disabled", palette.TEXT_DIM)],
                  arrowcolor=[
                      ("readonly", palette.TEXT),
                      ("active", palette.TEXT_BRIGHT)])
        # 下拉列表本身是原生 tk listbox，只能通过 option 数据库上色
        self.root.option_add("*TCombobox*listbox.background", palette.BG_SOFT)
        self.root.option_add("*TCombobox*listbox.foreground", palette.TEXT)
        self.root.option_add("*TCombobox*listbox.selectBackground", palette.ACCENT_DIM)
        self.root.option_add("*TCombobox*listbox.selectForeground", palette.TEXT_BRIGHT)

    # -- 明暗主题切换 ----------------------------------------------

    def _apply_mode(self, mode: str) -> None:
        """切换明亮 / 暗色主题：改 palette 常量、重刷样式并给已有控件换色。"""
        before = palette.snapshot()
        palette.set_mode(mode)
        after = palette.snapshot()
        # 只映射值有变化的颜色；字符串色值用于控件换色（HEAT_* 是元组，重绘时生效）
        mapping = {before[k]: after[k] for k in before
                   if before[k] != after[k] and isinstance(before[k], str)}
        self.config.ui_mode = palette.get_mode()
        try:
            self.config.save()
        except Exception:
            pass
        # ttk 控件跟着样式表走，重跑一遍即可
        try:
            self._build_style()
        except Exception:
            pass  # 样式刷新失败也不能阻断后续换色
        # 经典 tk 控件按"旧色→新色"映射就地换色
        self._recolor(self.root, mapping)
        # 剪贴板文本的标签与选择配色单独刷新
        self._apply_clip_colors()
        # 热力图按键底色 / 计数文字都画在画布里，用新配色整体重绘
        try:
            self.main_map.configure(background=palette.BG_PANEL)
            self.main_map._signature = None
            self.main_map._rebuild()
        except Exception:
            pass
        # 已打开的设置窗口直接关掉，随后用新配色重开
        try:
            old = getattr(self, "_hotkey_win", None)
            if old is not None and old.winfo_exists():
                old.destroy()
            self._hotkey_win = None
        except Exception:
            pass

    def _recolor(self, widget: tk.Misc, mapping: dict[str, str]) -> None:
        """递归遍历控件树，把经典 tk 控件上旧主题的颜色换成新主题对应值。"""
        for option in ("background", "foreground", "selectbackground",
                       "selectforeground", "insertbackground"):
            try:
                # cget 可能返回不可哈希的 Tcl_Obj，必须转 str 才能查表
                current = str(widget.cget(option))
            except tk.TclError:
                continue
            if current in mapping:
                try:
                    widget.configure(**{option: mapping[current]})
                except tk.TclError:
                    pass
        for child in widget.winfo_children():
            self._recolor(child, mapping)

    def _apply_clip_colors(self) -> None:
        """按当前主题刷新剪贴板文本的标签配色。

        Text 的 tag 颜色不是控件选项，主题映射覆盖不到；
        每次刷新剪贴板时都调一次，保证动作词（复制 / 粘贴）等
        始终与当前明暗主题对比正常。
        """
        try:
            self.clipboard_list.tag_configure("dim", foreground=palette.TEXT_DIM)
            self.clipboard_list.tag_configure("bright", foreground=palette.TEXT_BRIGHT)
            self.clipboard_list.tag_configure("clipimg", foreground=palette.ACCENT)
            self.clipboard_list.tag_configure("clippath", foreground=palette.ACCENT)
        except Exception:
            pass

    # -- 图标按钮与悬停提示 ----------------------------------------

    def _icon_button(self, parent: ttk.Frame, glyph: str, command: Callable[[], None],
                     tip: str | Callable[[], str], style: str = "Icon.TButton") -> ttk.Button:
        """创建图标按钮；tip 可以是字符串，也可以是返回字符串的函数（状态会变）。"""
        # 固定宽度：MDL2 字形测量宽度过大，须显式控制按钮宽度、字形居中显示
        btn = ttk.Button(parent, text=glyph, command=command, style=style, width=4)
        btn.pack(side="left", padx=3)
        self._attach_tooltip(btn, tip)
        return btn

    def _attach_tooltip(self, widget: tk.Widget, tip: str | Callable[[], str]) -> None:
        widget.bind("<Enter>", lambda _e: self._show_tooltip(widget, tip))
        widget.bind("<Leave>", lambda _e: self._hide_tooltip())
        widget.bind("<ButtonPress>", lambda _e: self._hide_tooltip())

    def _show_tooltip(self, widget: tk.Widget, tip: str | Callable[[], str]) -> None:
        self._hide_tooltip()
        try:
            text = str(tip()) if callable(tip) else str(tip)
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            popup = tk.Toplevel(self.root)
            popup.wm_overrideredirect(True)
            try:
                popup.attributes("-topmost", True)
            except Exception:
                pass
            popup.wm_geometry("+%d+%d" % (x, y))
            tk.Label(popup, text=text, background=palette.BG_SOFT,
                     foreground=palette.TEXT_BRIGHT, font=(self.font_family, 9),
                     padx=8, pady=3).pack()
            self._tooltip = popup
        except Exception:
            self._tooltip = None

    def _hide_tooltip(self) -> None:
        popup = self._tooltip
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass
            self._tooltip = None

    def _pause_tip(self) -> str:
        try:
            return "恢复记录" if self.tracker.paused else "暂停记录"
        except Exception:
            return "暂停 / 恢复记录"

    def _overlay_tip(self) -> str:
        return "关闭浮层" if self._overlay_visible() else "按键浮层"

    def _overlay_visible(self) -> bool:
        overlay = self.overlay
        try:
            return bool(overlay.visible) if overlay is not None else False
        except Exception:
            return False

    def _build_layout(self) -> None:
        root = ttk.Frame(self.root, style="Root.TFrame", padding=(14, 12))
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root, style="Root.TFrame")
        header.pack(fill="x")
        title_box = ttk.Frame(header, style="Root.TFrame")
        title_box.pack(side="left")
        tk.Frame(title_box, background=palette.ACCENT, width=3).pack(side="left", fill="y", padx=(0, 10))
        ttk.Label(title_box, text="KeyLens 按键记录器", style="Title.TLabel").pack(side="left")
        self.subtitle_label: ttk.Label = ttk.Label(
            title_box, text="本机自用 · 数据只存本地", style="Subtitle.TLabel")
        self.subtitle_label.pack(side="left", padx=(10, 0), pady=(7, 0))

        buttons = ttk.Frame(header, style="Root.TFrame")
        buttons.pack(side="right")
        # 图标语义（Segoe MDL2 Assets）：E769 暂停 / E768 播放 / E765 键盘 /
        # E9F9 文档 / E838 打开文件夹 / E713 设置 / E74D 删除
        self.pause_button: ttk.Button = self._icon_button(
            buttons, "\uE769", self.toggle_pause, self._pause_tip,
            style="Icon.Primary.TButton")
        self.overlay_button: ttk.Button = self._icon_button(
            buttons, "\uE765", self.toggle_overlay, self._overlay_tip)
        self._icon_button(buttons, "\uE9F9", self.export_report, "生成报告")
        self._icon_button(buttons, "\uE838", self.open_data_dir, "打开数据目录")
        self._icon_button(buttons, "\uE713", self._open_settings, "设置")
        self._icon_button(buttons, "\uE74D", self.clear_records, "清除记录",
                          style="Icon.Danger.TButton")

        status_row = ttk.Frame(root, style="Root.TFrame")
        status_row.pack(fill="x", pady=(8, 2))
        self.status_dot: tk.Label = tk.Label(
            status_row, text="●", background=palette.BG, foreground=palette.OK,
            font=(self.font_family, 11))
        self.status_dot.pack(side="left")
        self.status_label: ttk.Label = ttk.Label(status_row, text="正在启动…", style="Status.TLabel", anchor="w")
        self.status_label.pack(side="left", padx=(6, 0))
        # 版本号贴在状态栏右端：不起眼但一直在，报问题时一眼能说出是哪个版本
        self.version_label: ttk.Label = ttk.Label(
            status_row, text="v%s" % __version__, style="Subtitle.TLabel")
        self.version_label.pack(side="right")

        self.mask_banner: tk.Label = tk.Label(
            root, text="", background=palette.WARN_BG, foreground=palette.WARN_TEXT,
            font=(self.font_family, 9), anchor="w", padx=8, pady=3,
        )

        cards = ttk.Frame(root, style="Root.TFrame")
        # 指标卡整体压矮一点：省下的高度全给下面的键盘热力图（键区是高度受限的）
        cards.pack(fill="x", pady=(4, 6))
        self.metrics: dict[str, tk.Label] = {}
        specs = [
            ("day_total", "今日按键", palette.ACCENT),
            ("session_total", "本会话按键", palette.ACCENT),
            ("kpm", "最近 1 分钟", palette.TEXT_BRIGHT),
            ("clip", "复制 / 粘贴", palette.OK),
            ("active", "本会话时长", palette.TEXT_BRIGHT),
        ]
        for idx, (key, caption, tint) in enumerate(specs):
            # 手工卡片：1px 描边 + 顶部彩色窄条 + 彩点标题 + 大数字，观感更精致
            card = tk.Frame(cards, background=palette.BG_PANEL,
                            highlightbackground=palette.BORDER, highlightthickness=1)
            card.grid(row=0, column=idx, sticky="nsew", padx=5)
            cards.columnconfigure(idx, weight=1)
            tk.Frame(card, background=tint, height=2).pack(fill="x")
            inner = tk.Frame(card, background=palette.BG_PANEL)
            inner.pack(fill="both", expand=True, padx=12, pady=(6, 7))
            head = tk.Frame(inner, background=palette.BG_PANEL)
            head.pack(fill="x")
            tk.Label(head, text="●", background=palette.BG_PANEL, foreground=tint,
                     font=(self.font_family, 8)).pack(side="left")
            tk.Label(head, text=caption, background=palette.BG_PANEL, foreground=palette.TEXT_DIM,
                     font=(self.font_family, 9)).pack(side="left", padx=(5, 0))
            value = tk.Label(inner, text="0", background=palette.BG_PANEL, foreground=tint,
                             font=(self.font_family, 22, "bold"))
            value.pack(anchor="w", pady=(3, 0))
            self.metrics[key] = value

        # 竖向布局改用 grid：键盘区随窗口伸缩，底部"时间线 / 剪贴板"两块有最小高度。
        # 以前这几块是依次 pack 的，窗口不够高时最后 pack 的记录面板会被直接裁掉，
        # 只剩一两行；现在空间不够先压缩键盘区（画布会自己换小号字重画），
        # 记录面板始终保持可用高度。
        content = ttk.Frame(root, style="Root.TFrame")
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        # 键盘区是"高度受限"的：它的宽高比约 4:1，而画布通常是 5:1，
        # 高度一卡住键就放不大（实测 1080x690 时键盘只占 67% 宽度却占满 94% 高度，
        # 左右各空一大片）。所以垂直伸缩时给键盘区 3 份、记录面板 1 份，
        # 让键盘先长高、再把宽度填满；记录面板保持 TL_MIN_H 的可用高度。
        content.rowconfigure(0, weight=3)  # 键盘热力图
        content.rowconfigure(1, weight=0)  # 最常按的键 / 按键分布
        content.rowconfigure(2, weight=1, minsize=self.TL_MIN_H)  # 时间线 / 剪贴板记录

        maps = ttk.Frame(content, style="Root.TFrame")
        maps.grid(row=0, column=0, sticky="nsew")
        maps.columnconfigure(0, weight=1)
        maps.rowconfigure(0, weight=1)

        # 全部键区合并在同一块画布，按真实键盘排布：
        # 左列 = 功能键行 + 主键盘；右两列 = 控制键簇 + 数字键区（与主键盘区顶部对齐）
        self.main_map: HeatmapCanvas = HeatmapCanvas(
            maps,
            [("功能键区", [keymap.FUNCTION_ROW]), ("主键盘区", keymap.KEYBOARD_ROWS)],
            side_columns=[
                # 控制键区列：标题与"功能键区"同排（顶部），PrtSc 行与功能键行同高；
                # 空标题占位维持后面各行与小键盘逐行对齐
                ([("控制键区", [keymap.NAV_ROWS[0]]), (" ", keymap.NAV_ROWS[1:])], 0),
                # 数字键区：从主键盘区顶部开始，NumLock 行与数字行同高
                ([("数字键区", keymap.NUMPAD_ROWS)], 1),
            ],
            # 键宽下限要足够小：窗口很矮时键盘区会被压扁，
            # 下限太大就会把最后一行顶出画布（下限 18px 时整块还能完整放下）
            min_unit=18.0,
            # 上限只用来兜住"超大窗口"：键大小实际由画布尺寸反推，
            # 上限给足，宽屏上键盘才能一路撑满画布宽度
            max_unit=96.0,
        )
        self.main_map.set_font(self.font_family)
        self.main_map.grid(row=0, column=0, sticky="nsew")

        bottom = ttk.Frame(content, style="Root.TFrame")
        bottom.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)

        top_card = ttk.Frame(bottom, style="Card.TFrame", padding=(10, 8))
        top_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(top_card, text="最常按的键（今日）", style="PanelTitle.TLabel").pack(anchor="w")
        self.top_keys_label: ttk.Label = ttk.Label(top_card, text="—", style="Card.TLabel", justify="left")
        self.top_keys_label.pack(anchor="w", pady=(2, 0))

        proc_card = ttk.Frame(bottom, style="Card.TFrame", padding=(10, 8))
        proc_card.grid(row=0, column=1, sticky="nsew")
        ttk.Label(proc_card, text="按键分布（按前台应用，今日）", style="PanelTitle.TLabel").pack(anchor="w")
        self.proc_label: ttk.Label = ttk.Label(proc_card, text="—", style="Card.TLabel", justify="left")
        self.proc_label.pack(anchor="w", pady=(2, 0))

        tl_row = ttk.Frame(content, style="Root.TFrame")
        # 参与垂直扩展：窗口拉大/最大化时两个记录面板跟着变高；
        # 窗口不够高时也有最小高度（见 content 的 minsize），不会被裁掉
        tl_row.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        tl_row.columnconfigure(0, weight=1)
        tl_row.columnconfigure(1, weight=1)
        tl_row.rowconfigure(0, weight=1)

        keys_card = ttk.Frame(tl_row, style="Card.TFrame", padding=(10, 12))
        keys_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        tl_head = ttk.Frame(keys_card, style="Head.TFrame")
        tl_head.pack(fill="x")
        ttk.Label(
            tl_head,
            text="按键时间线",
            style="PanelTitle.TLabel",
        ).pack(side="left")
        self.tl_filter_box: ttk.Combobox = ttk.Combobox(
            tl_head,
            textvariable=self.tl_filter,
            values=[_TL_ALL],
            state="readonly",
            # 窄一点：最小窗口(960)时三个控件并排会挤爆标题行
            width=10,
            style="Filter.TCombobox",
        )
        self.tl_filter_box.pack(side="right")
        self.tl_filter_box.bind("<<ComboboxSelected>>", self._on_tl_filter_change)
        self.tl_day_box: ttk.Combobox = ttk.Combobox(
            tl_head,
            textvariable=self.tl_day,
            values=[_DAY_TODAY],
            state="readonly",
            width=6,
            style="Filter.TCombobox",
        )
        self.tl_day_box.pack(side="right", padx=(6, 0))
        self.tl_day_box.bind("<<ComboboxSelected>>", self._on_tl_day_change)
        self.timeline_list: tk.Listbox = tk.Listbox(
            keys_card,
            height=7,
            activestyle="none",
            selectmode="browse",
            background=palette.BG_SOFT,
            foreground=palette.TEXT,
            selectbackground=palette.ACCENT_DIM,
            selectforeground="#ffffff",  # 选中底色两种主题都是深蓝，固定白字对比稳定
            highlightthickness=0,
            bd=0,
            font=(self.font_family, 10),
        )
        self.timeline_list.pack(fill="both", expand=True, pady=(6, 0))

        clip_card = ttk.Frame(tl_row, style="Card.TFrame", padding=(10, 12))
        clip_card.grid(row=0, column=1, sticky="nsew")
        clip_head = ttk.Frame(clip_card, style="Head.TFrame")
        clip_head.pack(fill="x")
        ttk.Label(
            clip_head,
            text="剪贴板记录",
            style="PanelTitle.TLabel",
        ).pack(side="left")
        self.clip_filter_box: ttk.Combobox = ttk.Combobox(
            clip_head,
            textvariable=self.clip_filter,
            values=[_TL_ALL],
            state="readonly",
            width=10,
            style="Filter.TCombobox",
        )
        self.clip_filter_box.pack(side="right")
        self.clip_filter_box.bind("<<ComboboxSelected>>", self._on_clip_filter_change)
        self.clip_day_box: ttk.Combobox = ttk.Combobox(
            clip_head,
            textvariable=self.clip_day,
            values=[_DAY_TODAY],
            state="readonly",
            width=6,
            style="Filter.TCombobox",
        )
        self.clip_day_box.pack(side="right", padx=(6, 0))
        self.clip_day_box.bind("<<ComboboxSelected>>", self._on_clip_day_change)
        self.clipboard_list: tk.Text = tk.Text(
            clip_card,
            height=7,
            wrap="word",
            state="disabled",
            background=palette.BG_SOFT,
            foreground=palette.TEXT,
            selectbackground=palette.ACCENT_DIM,
            selectforeground="#ffffff",  # 选中底色两种主题都是深蓝，固定白字对比稳定
            highlightthickness=0,
            bd=0,
            font=(self.font_family, 10),
        )
        self.clipboard_list.pack(fill="both", expand=True, pady=(6, 0))
        # 图片记录：缩略图单击预览（不改剪贴板）；完整路径高亮、双击预览；
        # 「复制图片」放到右键菜单里显式操作
        self._apply_clip_colors()
        for tag in ("clipimg", "clippath"):
            self.clipboard_list.tag_bind(tag, "<Enter>", lambda _e: self.clipboard_list.configure(cursor="hand2"))
            self.clipboard_list.tag_bind(tag, "<Leave>", lambda _e: self.clipboard_list.configure(cursor=""))
        self.clipboard_list.tag_bind("clipimg", "<Button-1>", self._on_clip_image_click)
        self.clipboard_list.tag_bind("clippath", "<Double-Button-1>", self._on_clip_image_click)
        self.clipboard_list.bind("<Double-Button-1>", self._on_clip_open_image)
        self.clipboard_list.bind("<Button-3>", self._on_clip_right_click)

    def _set_icon(self) -> None:
        """窗口与任务栏图标：优先 app.ico，其次 Pillow 现画一个兜底。

        仅 iconphoto 在 Windows 任务栏上常回落成 python.exe 的默认图标，
        所以先用 iconbitmap 加载 .ico，并设置 AppUserModelID 让任务栏
        把本进程当作独立应用（不再沿用宿主 python.exe 的图标）。
        """
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "KeyLens.App")
            except Exception:
                pass
        try:
            ico = self._find_app_icon()
            if ico:
                self.root.iconbitmap(ico)
                return
        except Exception:
            pass
        try:
            from PIL import Image, ImageDraw, ImageTk

            image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((3, 8, 29, 25), radius=4, fill=palette.ACCENT)
            draw.rectangle((14, 3, 18, 10), fill=palette.ACCENT)
            self._icon: Any = ImageTk.PhotoImage(image)
            self.root.iconphoto(True, self._icon)  # pyright: ignore[reportArgumentType]
        except Exception:
            pass

    @staticmethod
    def _find_app_icon() -> str:
        """按优先级定位 app.ico。

        源码运行：项目根 `tools\\app.ico` → 包目录 `app.ico`；
        打包运行（PyInstaller onedir）：解包目录 `_internal\\app.ico` → exe 同目录 `app.ico`。
        注意 `_MEIPASS` 是**目录**，必须再拼上文件名，否则 isfile() 永远为假。
        """
        import os
        import sys

        here = os.path.dirname(os.path.abspath(__file__))  # ...\\keylens\\ui
        pkg_dir = os.path.dirname(here)                    # ...\\keylens
        root_dir = os.path.dirname(pkg_dir)                # 项目根
        candidates = [
            os.path.join(root_dir, "tools", "app.ico"),
            os.path.join(pkg_dir, "app.ico"),
            os.path.join(getattr(sys, "_MEIPASS", "") or "", "app.ico"),
            os.path.join(os.path.dirname(getattr(sys, "executable", "") or ""), "app.ico"),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        return ""

    # -- 托盘与显示 / 隐藏 ----------------------------------------

    def _setup_tray(self) -> None:
        """系统托盘：左键 / 全局热键（默认 Ctrl+Alt+K，可自定义）切换显示隐藏。"""
        hotkey = (getattr(self.config, "toggle_hotkey", "") or DEFAULT_HOTKEY).strip()
        self._hotkey_text: str = hotkey
        # 副标题里的快捷键提示与实际配置保持一致（自定义后自动跟随）
        try:
            self.subtitle_label.configure(text="本机自用 · 数据只存本地 · %s 显示/隐藏" % hotkey)
        except Exception:
            pass
        self.tray_menu: tk.Menu = tk.Menu(self.root, tearoff=0)
        self._tray_toggle_index: int = (self.tray_menu.index("end") or 0) + 1  # = 1，第一个菜单项
        self.tray_menu.add_command(label="显示 / 隐藏主界面（%s）" % hotkey, command=self.toggle_visible)
        self.tray_menu.add_separator()
        self.tray_menu.add_command(label="设置…", command=self._open_settings)
        self.tray_menu.add_separator()
        self.tray_menu.add_command(label="退出 KeyLens", command=self.on_quit)

        root = self.root

        def from_tray(callback: Callable[..., None]) -> Callable[..., None]:
            """托盘线程回调 → Tk 主线程（root.after 转发）。"""
            def runner(*args: Any) -> None:
                try:
                    root.after(0, lambda: callback(*args))
                except Exception:
                    pass
            return runner

        self.tray: TrayIcon = TrayIcon(
            on_toggle=from_tray(self.toggle_visible),
            on_menu=from_tray(self._on_tray_menu),
            on_quit=from_tray(self.on_quit),
            hotkey=hotkey,
            tooltip=default_tooltip(hotkey),
        )
        if not self.tray.start():
            message = self.tray.error or "托盘启动失败"
            try:
                self.status_label.configure(text=str(self.status_label.cget("text") or "") + " ｜ " + message)
            except Exception:
                pass

    def _open_settings(self) -> None:
        """设置窗口：显示/隐藏快捷键 + 数据目录（主界面齿轮按钮与托盘菜单共用）。"""
        # 已开着就只带到前台，避免叠出多个设置窗
        old = getattr(self, "_hotkey_win", None)
        if old is not None and old.winfo_exists():
            try:
                old.deiconify()
                old.lift()
                old.focus_force()
                return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        self._hotkey_win = win
        win.title("设置")
        win.configure(background=palette.BG)
        win.resizable(False, False)
        win.transient(self.root)
        # Toplevel 不会自动继承主窗口图标，需显式设置（否则显示 Tk 默认图标）
        try:
            ico = self._find_app_icon()
            if ico:
                win.iconbitmap(ico)
        except Exception:
            pass

        current = getattr(self, "_hotkey_text", DEFAULT_HOTKEY)
        captured = tk.StringVar(master=win, value=current)
        note = tk.StringVar(master=win, value="点击下方输入框，直接按下想要的组合键")

        body = ttk.Frame(win, style="Card.TFrame", padding=20)
        body.pack(fill="both", expand=True, padx=14, pady=14)

        # -- 显示 / 隐藏快捷键 ----------------------------------------
        ttk.Label(body, text="显示 / 隐藏快捷键", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(body, text="当前快捷键：", style="Note.TLabel").pack(anchor="w", pady=(10, 0))
        entry = ttk.Entry(body, textvariable=captured, width=24, justify="center")
        entry.pack(fill="x", pady=(4, 6))
        entry.focus_set()
        ttk.Label(body, textvariable=note, style="Note.TLabel", wraplength=330,
                  justify="left").pack(anchor="w", pady=(0, 10))
        # 按钮行用无边框容器（Card.TFrame 的描边会在行尾空白处露出空盒）
        hotkey_row = ttk.Frame(body, style="Head.TFrame")
        hotkey_row.pack(fill="x")
        ttk.Button(hotkey_row, text="保存", style="Primary.TButton",
                   command=lambda: self._apply_hotkey_change(
                       captured.get(), note, win)).pack(side="left", padx=(0, 8))
        ttk.Button(hotkey_row, text="恢复默认", command=lambda: (
            captured.set(DEFAULT_HOTKEY),
            self._apply_hotkey_change(DEFAULT_HOTKEY, note, win))).pack(side="left")

        ttk.Separator(body).pack(fill="x", pady=14)

        # -- 外观（明亮 / 暗色）----------------------------------------
        ttk.Label(body, text="外观", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(body, text="切换后立即生效并保存，无需重启", style="Note.TLabel").pack(
            anchor="w", pady=(6, 8))
        theme_row = ttk.Frame(body, style="Head.TFrame")
        theme_row.pack(fill="x")
        self._theme_buttons: dict[str, ttk.Button] = {
            "dark": ttk.Button(theme_row, text="暗色",
                               command=lambda: self._switch_mode("dark")),
            "light": ttk.Button(theme_row, text="明亮",
                                command=lambda: self._switch_mode("light")),
        }
        self._theme_buttons["dark"].pack(side="left", padx=(0, 8))
        self._theme_buttons["light"].pack(side="left")
        self._highlight_mode_buttons()

        ttk.Separator(body).pack(fill="x", pady=14)

        # -- 按键浮层 --------------------------------------------------
        ttk.Label(body, text="按键浮层", style="PanelTitle.TLabel").pack(anchor="w")
        keys_only_var = tk.BooleanVar(master=win, value=bool(self.config.overlay_keys_only))
        _CheckRow(
            body, "只显示按键（不显示面板背景与标题）", keys_only_var,
            lambda: self._apply_overlay_keys_only(keys_only_var.get()),
            self.font_family,
        ).pack(fill="x", pady=(8, 3))
        # 说明拆成两行：一行讲效果、一行讲怎么关。原来挤在一行里
        # wraplength 会把句号甩到下一行单独一个字，看着像排版坏了。
        ttk.Label(body, style="Note.TLabel", wraplength=330, justify="left",
                  text="· 浮层只剩按键本身，背景透明，宽度随内容自动伸缩"
                  ).pack(anchor="w")
        ttk.Label(body, style="Note.TLabel", wraplength=330, justify="left",
                  text="· 关闭浮层：主界面按钮、快捷键，或在浮层上右键"
                  ).pack(anchor="w", pady=(2, 0))

        ttk.Separator(body).pack(fill="x", pady=14)

        # -- 数据目录 --------------------------------------------------
        ttk.Label(body, text="数据目录", style="PanelTitle.TLabel").pack(anchor="w")
        # 路径用只读输入框呈现：点击全选、Ctrl+C 可复制，右键有复制/打开菜单
        path_var = tk.StringVar(master=win, value=self.config.data_dir)
        path_entry = ttk.Entry(body, textvariable=path_var, state="readonly")
        path_entry.pack(fill="x", pady=(6, 8))
        path_entry.bind("<FocusIn>", lambda _e: path_entry.select_range(0, "end"))
        path_entry.bind("<Button-1>", lambda _e: path_entry.select_range(0, "end"))
        path_entry.bind("<Control-a>", lambda _e: (path_entry.select_range(0, "end"), "break")[1])
        path_menu = tk.Menu(path_entry, tearoff=0)
        path_menu.add_command(label="复制路径", command=lambda: (
            self.root.clipboard_clear(),
            self.root.clipboard_append(path_var.get())))
        path_menu.add_command(label="打开目录", command=self.open_data_dir)
        path_entry.bind("<Button-3>", lambda e: path_menu.tk_popup(e.x_root, e.y_root))
        try:
            self._attach_tooltip(path_entry, "点击全选，Ctrl+C 复制；右键可复制 / 打开目录")
        except Exception:
            pass
        dir_row = ttk.Frame(body, style="Head.TFrame")
        dir_row.pack(fill="x")
        ttk.Button(dir_row, text="更改…", command=self.change_data_dir).pack(side="left", padx=(0, 8))
        ttk.Button(dir_row, text="打开目录", command=self.open_data_dir).pack(side="left")

        ttk.Separator(body).pack(fill="x", pady=14)
        # 页脚：版本号在左、关闭在右，和 Windows「属性」里的版本号对得上
        foot = ttk.Frame(body, style="Head.TFrame")
        foot.pack(fill="x")
        ttk.Label(foot, text="KeyLens 按键记录器", style="Version.TLabel").pack(side="left")
        ttk.Label(foot, text="v%s" % __version__, style="Version.TLabel").pack(
            side="left", padx=(6, 0))
        ttk.Button(foot, text="关闭", command=win.destroy).pack(side="right")

        # 修饰键按下时不算完整组合，只刷新提示
        _MOD_KEYS = {"Control_L", "Control_R", "Shift_L", "Shift_R",
                     "Alt_L", "Alt_R", "Meta_L", "Meta_R", "Win_L", "Win_R"}

        def on_key(event: Any) -> str | None:
            # 焦点在路径输入框时走默认编辑行为（选择 / 复制），不当作快捷键捕获
            try:
                if win.focus_get() is path_entry:
                    return None
            except Exception:
                pass
            keysym = event.keysym
            if keysym in _MOD_KEYS:
                return
            parts = []
            if event.state & 0x4:    # Control
                parts.append("Ctrl")
            if event.state & 0x1:    # Shift
                parts.append("Shift")
            if event.state & 0x8:    # Alt（Tk 里 Alt = Mod1 掩码）
                parts.append("Alt")
            if event.state & 0x40:   # Win（Tk 里 Win/Super = Mod4 掩码）
                parts.append("Win")
            parts.append(keysym.upper() if len(keysym) == 1 else keysym)
            captured.set("+".join(parts))
            note.set("按“保存”生效；快捷键必须带 Ctrl / Alt / Shift / Win 修饰键")
            return "break"

        # 内容定尺寸后，把窗口居中到主窗口上（否则 Windows 默认摆在屏幕左上角）
        win.update_idletasks()
        try:
            root_x = self.root.winfo_rootx()
            root_y = self.root.winfo_rooty()
            root_w = self.root.winfo_width()
            root_h = self.root.winfo_height()
            win_w = win.winfo_reqwidth()
            win_h = win.winfo_reqheight()
            x = root_x + max(0, (root_w - win_w) // 2)
            y = root_y + max(0, (root_h - win_h) // 3)
            win.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

        win.bind("<KeyPress>", on_key)

    def _apply_overlay_keys_only(self, value: bool) -> None:
        """保存"只显示按键"开关，并立刻作用到已打开的浮层。"""
        self.config.overlay_keys_only = bool(value)
        try:
            self.config.save()
        except Exception:
            pass
        overlay = self.overlay
        if overlay is None:
            return
        try:
            overlay.set_keys_only(self.config.overlay_keys_only)
        except Exception:
            pass

    def _switch_mode(self, mode: str) -> None:
        """切换主题：其余界面就地换色；设置窗口销毁后用新配色重开。"""
        if palette.get_mode() == mode:
            return
        self._apply_mode(mode)
        self._open_settings()

    def _highlight_mode_buttons(self) -> None:
        """当前主题的按钮用主色样式，另一个用常规样式。"""
        for name, btn in getattr(self, "_theme_buttons", {}).items():
            try:
                btn.configure(
                    style="Primary.TButton" if name == palette.get_mode() else "TButton")
            except tk.TclError:
                pass

    def _apply_hotkey_change(self, value: str, note: tk.StringVar, win: tk.Toplevel) -> None:
        """校验并保存新快捷键：写回 config、换绑全局热键、同步托盘提示与菜单。"""
        value = (value or "").strip()
        if not parse_hotkey(value):
            note.set("无法识别的快捷键“%s”：需要修饰键 + 字母 / 数字 / F1~F24" % value)
            return
        # 先换绑托盘热键，注册失败（被占用）就不改配置
        if self.tray and self.tray.running:
            self.tray.set_hotkey(value)
            if not self.tray.hotkey:
                note.set("快捷键 %s 注册失败，可能已被其他程序占用，请换一个" % value)
                return
            self.tray.set_tooltip(default_tooltip(value))
        self._hotkey_text = value
        self.config.toggle_hotkey = value
        try:
            self.config.save()
        except Exception:
            pass
        try:
            self.tray_menu.entryconfig(self._tray_toggle_index,
                                       label="显示 / 隐藏主界面（%s）" % value)
        except Exception:
            pass
        # 副标题里的快捷键提示同步更新
        try:
            self.subtitle_label.configure(text="本机自用 · 数据只存本地 · %s 显示/隐藏" % value)
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass
        try:
            self.status_label.configure(text="显示/隐藏快捷键已更新为 %s" % value)
        except Exception:
            pass

    def toggle_visible(self) -> None:
        """显示 / 隐藏主界面（托盘左键与全局热键都走这里）。"""
        try:
            if self.root.state() == "withdrawn":
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            else:
                self.root.withdraw()
        except Exception:
            pass

    def _on_tray_menu(self, x: int, y: int) -> None:
        """托盘右键菜单（在鼠标位置弹出）。"""
        try:
            self.tray_menu.tk_popup(x, y)
        except Exception:
            pass
        finally:
            try:
                self.tray_menu.grab_release()
            except Exception:
                pass

    # -- 定时刷新 -------------------------------------------------

    def _schedule_refresh(self) -> None:
        """由 tracker 在后台线程回调，转回 Tk 主线程。"""
        try:
            self.root.after(0, self._refresh)
        except Exception:
            pass

    def _schedule_live_refresh(self) -> None:
        """按键级实时刷新（钩子线程调用）：合并成最多 10Hz 的轻量刷新。

        只重画键盘热力图、计数卡和状态栏这些纯内存部分；
        时间线 / 剪贴板 / 当日汇总仍由 1Hz 的整窗刷新负责，避免频繁查库。
        """
        if self._closing or self._live_job is not None:
            return
        try:
            self._live_job = self.root.after(_LIVE_REFRESH_MS, self._refresh_live)
        except Exception:
            pass

    def _refresh_live(self) -> None:
        self._live_job = None
        if self._closing:
            return
        try:
            self._apply_live(self.tracker.live_stats())
        except Exception as exc:  # 界面永不因统计异常而退出
            try:
                self.status_label.configure(text="刷新出错：%s" % exc)
            except Exception:
                pass

    def _tick(self) -> None:
        if self._closing:
            return
        try:
            self._refresh()
        except Exception as exc:  # 界面永不因统计异常而退出
            try:
                self.status_label.configure(text="刷新出错：%s" % exc)
            except Exception:
                pass
        self.root.after(1000, self._tick)

    # -- 按键浮层（实时显示，不落库） ------------------------------

    def toggle_overlay(self) -> None:
        if self.overlay is None:
            from .osd import KeyOverlay

            self.overlay = KeyOverlay(
                self.root, self.font_family, on_close=self._overlay_closed,
                config=self.config,
            )
            # "只显示按键"模式：浮层只剩按键本身，背景透明
            try:
                self.overlay.set_keys_only(bool(self.config.overlay_keys_only))
            except Exception:
                pass
            self.tracker.set_key_observer(self.overlay.push)
        if self.overlay.visible:
            self.overlay.hide()
            self.overlay_button.configure(text="\uE765")
        else:
            self.overlay.show()
            self.overlay_button.configure(text="\uE8A7")
            if not self._overlay_ticking:
                # 浮层要 5Hz 刷新才有淡出效果，主界面 1Hz 太慢
                self._overlay_ticking = True
                self._overlay_tick()

    def _overlay_tick(self) -> None:
        if self._closing:
            return
        self._pump_overlay()
        self.root.after(200, self._overlay_tick)

    def _overlay_closed(self) -> None:
        try:
            self.overlay_button.configure(text="\uE765")
        except Exception:
            pass

    def _pump_overlay(self) -> None:
        """浮层刷新走 Tk 主线程：钩子线程只入队，绝不能直接碰控件。"""
        overlay = self.overlay
        if overlay is None or not overlay.visible:
            return
        try:
            overlay.set_paused(self.tracker.paused)
        except Exception:
            pass
        try:
            overlay.pump()
        except Exception as exc:  # 浮层出任何问题都不影响主界面
            try:
                self.status_label.configure(text="按键浮层出错：%s" % exc)
            except Exception:
                pass

    def _clip_state(self, stats: Any) -> str:
        cfg = self.config.clipboard
        if not cfg.enabled:
            return " ｜ 剪贴板：已关闭"
        if not cfg.track_copy:
            return " ｜ 剪贴板：只记粘贴"
        if stats.clipboard_active:
            return " ｜ 剪贴板监听：开启%s" % ("（存正文）" if cfg.store_text else "（只记类型与长度）")
        if stats.clipboard_error:
            return " ｜ 剪贴板监听失败：%s" % stats.clipboard_error
        return " ｜ 剪贴板监听：未运行"

    def _refresh(self) -> None:
        stats = self.tracker.live_stats()
        self._apply_live(stats)

        now = time.time()
        if (now - self._last_summary_load) > 15:
            self._last_summary_load = now
            try:
                self._summary = self.tracker.db.day_summary(self.tracker.today())
            except Exception:
                self._summary = None
        summary = self._summary
        if summary:
            # 只列前几名：这两张卡是"摘要"，行数一多（按键分布 6 行时高 147px）
            # 就把上面的键盘热力图挤矮一圈（键宽小 23%）。完整排行在 HTML 报告里。
            top = ", ".join(
                "%s %d" % (keymap.display(k), v)
                for k, v in list(summary.by_key.items())[:6]
                if k != "MASKED"
            )
            self.top_keys_label.configure(text=top or "—")
            procs = "\n".join(
                "%s  %d 次" % (name, count) for name, count in list(summary.by_process.items())[:3]
            )
            self.proc_label.configure(text=procs or "—")
        else:
            self.top_keys_label.configure(text="—")
            self.proc_label.configure(text="—")

        self._refresh_timeline()
        self._refresh_clipboard()

    def _apply_live(self, stats: Any) -> None:
        """轻量刷新：标题、状态栏、指标卡、键盘热力图。

        纯内存计算，10Hz 跑也不卡；时间线 / 剪贴板 / 当日汇总留给 1Hz 的 _refresh。
        """
        state = "已暂停" if stats.paused else "正在记录"
        if stats.remote:
            remote = "远程/监控软件输入中" + ("（%s）" % stats.remote_note if stats.remote_note else "")
        else:
            remote = "本地输入"
        title = "KeyLens 按键记录器 · %s · 今日 %d 次" % (state, stats.total_keys)
        if self.root.title() != title:
            self.root.title(title)

        detail = "状态：%s（%s）｜ 会话 #%s ｜ %s" % (
            state,
            stats.paused_reason or "运行中",
            stats.session_id if stats.session_id else "-",
            remote,
        )
        if stats.remote and stats.remote_note:
            detail += " ｜ 证据：%s" % stats.remote_note
        detail += self._clip_state(stats)
        detail += " ｜ 丢弃事件：%d" % self.tracker.dropped_events
        self.status_label.configure(text=detail)

        if stats.masking_now:
            self.mask_banner.configure(
                text="当前前台应用命中隐私掩码：只统计按键次数，不记录具体键位，也不记录剪贴板类型与长度。"
            )
            self.mask_banner.pack(fill="x", pady=(0, 4))
        else:
            self.mask_banner.pack_forget()

        self.metrics["day_total"].configure(text="{:,.0f}".format(stats.total_keys))
        self.metrics["session_total"].configure(text="{:,.0f}".format(stats.session_keys))
        self.metrics["kpm"].configure(text="%d" % stats.keys_last_minute)
        self.metrics["clip"].configure(text="%d / %d" % (stats.clip_copy, stats.clip_paste))
        minutes = int(stats.active_minutes)
        self.metrics["active"].configure(text="%d:%02d" % (minutes // 60, minutes % 60))

        counts = self.tracker.key_counts()
        self.main_map.update_values(counts)

        self.pause_button.configure(text="\uE768" if stats.paused else "\uE769")
        # 状态点：绿色=记录中，橙色=已暂停
        dot_color = palette.WARN_TEXT if stats.paused else palette.OK
        try:
            if str(self.status_dot.cget("foreground")) != dot_color:
                self.status_dot.configure(foreground=dot_color)
        except Exception:
            pass

    def _refresh_timeline(self) -> None:
        """把选定日期的按键按时间顺序追加到时间线列表里。

        只读数据库里已落库的记录（按 id 递增），掩码键不显示键位，
        只显示「隐私掩码 · 不记录内容」的明文说明，保证界面上也看不到口令序列。
        日期下拉可选「今天」或历史日期；程序下拉可按前台进程筛选。
        """
        try:
            sel_day = self.tl_day.get()
            day = self.tracker.today() if sel_day == _DAY_TODAY else sel_day
        except Exception:
            return
        if day != self._tl_day:
            # 切换日期（或跨天 / 首次）：清空并从该日期第一条重新开始
            self._tl_day = day
            self._tl_last_id = 0
            self.timeline_list.delete(0, "end")
        selected = self.tl_filter.get()
        only_proc = None if selected == _TL_ALL else selected
        try:
            conn = self.tracker.db.connect()
            try:
                days = [
                    r["day"]
                    for r in conn.execute(
                        "SELECT DISTINCT day FROM events ORDER BY day DESC LIMIT 30"
                    )
                ]
                procs = [
                    (r["process"] or _TL_UNKNOWN)
                    for r in conn.execute(
                        "SELECT process, COUNT(*) AS n FROM events WHERE day = ?"
                        " GROUP BY process ORDER BY n DESC LIMIT 40",
                        (day,),
                    )
                ]
                # 循环分批拉取：历史日期一次读完整天（至上限），今天保持增量节奏
                rows: list[Any] = []
                while True:
                    if only_proc is None:
                        batch = conn.execute(
                            "SELECT id, ts, key_id, process FROM events"
                            " WHERE day = ? AND id > ? ORDER BY id LIMIT 400",
                            (day, self._tl_last_id),
                        ).fetchall()
                    else:
                        batch = conn.execute(
                            "SELECT id, ts, key_id, process FROM events"
                            " WHERE day = ? AND process = ? AND id > ? ORDER BY id LIMIT 400",
                            (day, "" if only_proc == _TL_UNKNOWN else only_proc, self._tl_last_id),
                        ).fetchall()
                    if not batch:
                        break
                    self._tl_last_id = int(batch[-1]["id"])
                    rows.extend(batch)
                    if len(batch) < 400 or len(rows) >= 1600:
                        break
            finally:
                conn.close()
        except Exception:
            return
        self._sync_tl_days(days)
        self._sync_tl_procs(procs)
        if not rows:
            return
        for row in rows:
            when = time.strftime("%H:%M:%S", time.localtime(float(row["ts"])))
            key_id = row["key_id"]
            shown_key = _MASK_TEXT if key_id == "MASKED" else keymap.display(key_id)
            proc = row["process"] or _TL_UNKNOWN
            self.timeline_list.insert("end", "%s   %-10s  %s" % (when, shown_key, proc))
        extra = self.timeline_list.size() - self._tl_max_rows
        if extra > 0:
            self.timeline_list.delete(0, extra - 1)
        self.timeline_list.see("end")

    def _sync_tl_procs(self, procs: list[str]) -> None:
        """刷新筛选下拉里的程序列表（保留当前选择，避免下拉变空白）。"""
        cur = self.tl_filter.get()
        values = [_TL_ALL]
        seen = {_TL_ALL}
        for name in procs:
            if name and name not in seen:
                seen.add(name)
                values.append(name)
        if cur not in seen:
            values.append(cur)
        if values == self._tl_procs:
            return
        self._tl_procs = values
        try:
            self.tl_filter_box.configure(values=values)
        except Exception:
            pass

    def _sync_tl_days(self, days: list[str]) -> None:
        """刷新日期下拉（今天 + 有记录的历史日期，最近在前）。"""
        values = [_DAY_TODAY]
        seen = {_DAY_TODAY}
        for name in days:
            if name and name not in seen:
                seen.add(name)
                values.append(name)
        cur = self.tl_day.get()
        if cur not in seen:
            values.append(cur)
        if values == self._tl_days:
            return
        self._tl_days = values
        try:
            self.tl_day_box.configure(values=values)
        except Exception:
            pass

    def _on_tl_day_change(self, _event: Any = None) -> None:
        """切换日期后，时间线从该日期开头重新加载。"""
        self._tl_day = None
        self._tl_last_id = 0
        try:
            self.timeline_list.delete(0, "end")
        except Exception:
            pass
        self._refresh_timeline()

    def _on_tl_filter_change(self, _event: Any = None) -> None:
        """切换「按程序筛选」后，时间线从当天开头重新加载。"""
        self._tl_day = None
        self._tl_last_id = 0
        try:
            self.timeline_list.delete(0, "end")
        except Exception:
            pass
        self._refresh_timeline()

    def _clip_image_path_from_index(self, index: str) -> str:
        """解析指定位置所在记录行里的图片完整路径；非图片行返回空串。"""
        try:
            line = self.clipboard_list.get("%s linestart" % index, "%s lineend" % index)
        except Exception:
            return ""
        match = re.search(r"([A-Za-z]:\\.+?\.bmp)\s*$", line)
        if match:
            return match.group(1)
        # 兼容旧行（只显示文件名）：用统一目录拼回完整路径
        name = re.search(r"(clip-[\w-]+\.bmp)\s*$", line)
        if name:
            return os.path.join(clipboard.image_save_dir(), name.group(1))
        return ""

    def _open_clip_image(self, path: str) -> None:
        """只打开图片预览，不改动剪贴板内容（复制走右键菜单）。"""
        if not os.path.isfile(path):
            self.status_label.configure(text="图片临时文件已被清理：%s" % path)
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            pass
        self.status_label.configure(text="已打开预览（剪贴板未改动）：%s" % path)

    def _on_clip_image_click(self, _event: Any = None) -> str:
        """单击缩略图 / 双击路径：只打开预览。"""
        path = self._clip_image_path_from_index("current")
        if path:
            self._open_clip_image(path)
            return "break"
        return ""

    def _on_clip_open_image(self, _event: Any = None) -> None:
        """双击图片记录行：只打开预览，不改动剪贴板。"""
        path = self._clip_image_path_from_index("insert")
        if path:
            self._open_clip_image(path)

    def _on_clip_right_click(self, event: Any) -> str:
        """剪贴板面板右键菜单：打开 / 复制图片 / 复制路径 / 复制正文。"""
        try:
            index = self.clipboard_list.index("@%d,%d" % (event.x, event.y))
        except Exception:
            return ""
        path = self._clip_image_path_from_index(index)
        menu = tk.Menu(self.root, tearoff=0)
        if path:
            menu.add_command(label="打开图片", command=lambda p=path: self._open_clip_image(p))
            menu.add_command(label="复制图片（放回剪贴板）",
                             command=lambda p=path: self._copy_clip_image(p))
            menu.add_command(label="复制图片路径",
                             command=lambda p=path: self._copy_clip_text(p))
        else:
            try:
                line = self.clipboard_list.get("%s linestart" % index, "%s lineend" % index)
            except Exception:
                line = ""
            body = re.sub(r"^\S+\s+(复制|粘贴)\s+\[[^\]]+\]\s+", "", line).strip()
            if body and not body.startswith(_MASK_TEXT) and not body.startswith("图片 → "):
                menu.add_command(label="复制正文",
                                 command=lambda t=body: self._copy_clip_text(t))
        if not menu.index("end"):
            return ""
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return ""

    def _copy_clip_image(self, path: str) -> None:
        """只把图片放回剪贴板，不打开预览。"""
        if clipboard.set_clipboard_image(path):
            self.status_label.configure(text="图片已放回剪贴板：%s" % path)
        else:
            self.status_label.configure(text="图片放回剪贴板失败：%s" % path)

    def _copy_clip_text(self, text: str) -> None:
        """把一段文本放进系统剪贴板。"""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_label.configure(text="已复制到剪贴板")

    def _sync_clip_procs(self, procs: list[str]) -> None:
        """刷新剪贴板筛选下拉里的程序列表（保留当前选择，避免下拉变空白）。"""
        cur = self.clip_filter.get()
        values = [_TL_ALL]
        seen = {_TL_ALL}
        for name in procs:
            if name and name not in seen:
                seen.add(name)
                values.append(name)
        if cur not in seen:
            values.append(cur)
        if values == self._clip_procs:
            return
        self._clip_procs = values
        try:
            self.clip_filter_box.configure(values=values)
        except Exception:
            pass

    def _on_clip_filter_change(self, _event: Any = None) -> None:
        """切换剪贴板的「按程序筛选」后，从当天开头重新加载。"""
        self._cl_day = None
        self._cl_last_id = 0
        try:
            self.clipboard_list.configure(state="normal")
            self.clipboard_list.delete("1.0", "end")
            self.clipboard_list.configure(state="disabled")
        except Exception:
            pass
        self._refresh_clipboard()

    def _sync_clip_days(self, days: list[str]) -> None:
        """刷新剪贴板面板的日期下拉（今天 + 有记录的历史日期，最近在前）。"""
        values = [_DAY_TODAY]
        seen = {_DAY_TODAY}
        for name in days:
            if name and name not in seen:
                seen.add(name)
                values.append(name)
        cur = self.clip_day.get()
        if cur not in seen:
            values.append(cur)
        if values == self._cl_days:
            return
        self._cl_days = values
        try:
            self.clip_day_box.configure(values=values)
        except Exception:
            pass

    def _on_clip_day_change(self, _event: Any = None) -> None:
        """切换日期后，剪贴板面板从该日期开头重新加载。"""
        self._cl_day = None
        self._cl_last_id = 0
        try:
            self.clipboard_list.configure(state="normal")
            self.clipboard_list.delete("1.0", "end")
            self.clipboard_list.configure(state="disabled")
        except Exception:
            pass
        self._refresh_clipboard()

    def _refresh_clipboard(self) -> None:
        """把当天最新的复制 / 粘贴记录追加到剪贴板文本区里。

        使用 ``tk.Text`` 自动换行，所以长文本不会像 listbox 那样被横向截断。
        正文来自 ``clipboard.note``（需在 config.json 里把 ``clipboard.store_text``
        设为 true 才会落库）；掩码场景显示「隐私掩码 · 不记录内容」，不显示任何内容。
        日期下拉可选「今天」或历史日期；程序下拉可按前台进程筛选。
        """
        self._apply_clip_colors()  # 标签配色随当前主题自愈，防止切换后残留旧色
        try:
            sel_day = self.clip_day.get()
            day = self.tracker.today() if sel_day == _DAY_TODAY else sel_day
        except Exception:
            return
        if day != self._cl_day:
            self._cl_day = day
            self._cl_last_id = 0
            self._clip_thumbs.clear()
            self.clipboard_list.configure(state="normal")
            self.clipboard_list.delete("1.0", "end")
            self.clipboard_list.configure(state="disabled")
        selected = self.clip_filter.get()
        only_proc = None if selected == _TL_ALL else selected
        try:
            conn = self.tracker.db.connect()
            try:
                days = [
                    r["day"]
                    for r in conn.execute(
                        "SELECT DISTINCT day FROM clipboard ORDER BY day DESC LIMIT 30"
                    )
                ]
                procs = [
                    (r["process"] or _TL_UNKNOWN)
                    for r in conn.execute(
                        "SELECT process, COUNT(*) AS n FROM clipboard WHERE day = ?"
                        " GROUP BY process ORDER BY n DESC LIMIT 40",
                        (day,),
                    )
                ]
                # 循环分批拉取：历史日期一次读完整天（至上限），今天保持增量节奏
                rows: list[Any] = []
                while True:
                    if only_proc is None:
                        batch = conn.execute(
                            "SELECT id, ts, kind, content, note, masked, process FROM clipboard"
                            " WHERE day = ? AND id > ? ORDER BY id LIMIT 200",
                            (day, self._cl_last_id),
                        ).fetchall()
                    else:
                        batch = conn.execute(
                            "SELECT id, ts, kind, content, note, masked, process FROM clipboard"
                            " WHERE day = ? AND process = ? AND id > ? ORDER BY id LIMIT 200",
                            (day, "" if only_proc == _TL_UNKNOWN else only_proc, self._cl_last_id),
                        ).fetchall()
                    if not batch:
                        break
                    self._cl_last_id = int(batch[-1]["id"])
                    rows.extend(batch)
                    if len(batch) < 200 or len(rows) >= 1600:
                        break
            finally:
                conn.close()
        except Exception:
            return
        self._sync_clip_days(days)
        self._sync_clip_procs(procs)
        if not rows:
            return
        self.clipboard_list.configure(state="normal")
        for row in rows:
            self._cl_last_id = int(row["id"])
            when = time.strftime("%H:%M:%S", time.localtime(float(row["ts"])))
            kind = "复制" if row["kind"] == "copy" else "粘贴"
            proc = row["process"] or _TL_UNKNOWN
            if row["masked"] or row["content"] == "MASKED":
                body = _MASK_TEXT  # 掩码项也用明文说明，不显示成符号化的「（掩码）」
                note = ""
            else:
                note = (row["note"] or "").replace("\r", " ").replace("\n", " ").strip()
                if note:
                    if note.lower().endswith(".bmp"):
                        # 显示完整路径，路径本身可双击打开、可右键复制
                        body = "图片 → " + note
                    else:
                        body = note if len(note) <= 200 else note[:200] + "…"
                else:
                    body = _CLIP_DISPLAY.get(row["content"], row["content"]) or ""
            is_image = note.lower().endswith(".bmp")
            thumb = self._load_thumb(note) if is_image else None
            if thumb is not None:
                # 行首嵌入缩略图，单击即可打开预览
                self._clip_thumbs.append(thumb)
                start = self.clipboard_list.index("end-1c")
                self.clipboard_list.image_create("end", image=thumb)
                self.clipboard_list.tag_add("clipimg", start, "end-1c")
                self.clipboard_list.insert("end", " ")
            self.clipboard_list.insert("end", when + "  ", "dim")
            self.clipboard_list.insert("end", kind + "  ", "bright")
            self.clipboard_list.insert("end", "[%s]  " % proc, "dim")
            if is_image:
                self.clipboard_list.insert("end", body, "clippath")
                self.clipboard_list.insert("end", "\n")
            else:
                self.clipboard_list.insert("end", body + "\n")
        line_count = int(self.clipboard_list.index("end-1c").split(".")[0])
        extra = line_count - self._cl_max_rows
        if extra > 0:
            self.clipboard_list.delete("1.0", "%d.0" % (extra + 1))
        if len(self._clip_thumbs) > self._cl_max_rows:
            del self._clip_thumbs[: len(self._clip_thumbs) - self._cl_max_rows]
        self.clipboard_list.see("end")
        self.clipboard_list.configure(state="disabled")

    def _load_thumb(self, path: str):
        """加载图片临时文件的缩略图（64x48 以内）；失败返回 None。"""
        try:
            from PIL import Image, ImageTk

            img = Image.open(path)
            img.thumbnail((64, 48))
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # -- 动作 -----------------------------------------------------

    def toggle_pause(self) -> None:
        self.tracker.toggle_pause()
        self._refresh()

    def open_data_dir(self) -> None:
        path = self.config.data_dir
        try:
            os.makedirs(path, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def change_data_dir(self) -> None:
        """把数据目录迁移到用户选择的新位置（迁移期间短暂停止记录）。"""
        from tkinter import filedialog

        current = self.config.data_dir
        chosen = filedialog.askdirectory(title="选择新的数据存放目录", initialdir=current)
        if not chosen:
            return
        new_dir = os.path.normpath(chosen)
        if new_dir == os.path.normpath(current):
            return
        if not messagebox.askyesno(
            "更改数据目录",
            "将把现有数据（数据库、报告等）移动到：\n%s\n\n"
            "迁移期间会停止记录几秒钟，完成后自动恢复。\n确定继续吗？" % new_dir,
        ):
            return
        self.status_label.configure(text="正在迁移数据目录…")
        self.root.update_idletasks()
        # 1) 停止记录（写线程会把缓冲数据全部落库）
        self.tracker.stop()
        # 2) 搬运数据文件
        moved, errors = self._migrate_data(current, new_dir)
        # 3) 更新配置（写到所有可能出现 config.json 的位置，保证下次启动读得到）
        self.config.data_dir = new_dir
        self._write_config_everywhere(current)
        # 4) 指向新数据库并恢复记录
        try:
            self.tracker.db = db_module.Database(self.config.db_path)
            self.tracker.db.initialize()
        except Exception as exc:
            messagebox.showerror("更改数据目录", "新目录数据库初始化失败：%s" % exc)
            return
        self.tracker.resume()
        hook_ok = self.tracker.start()
        # 数据库已换，两个面板的增量游标作废
        self._tl_day = None
        self._tl_last_id = 0
        self._cl_day = None
        self._cl_last_id = 0
        summary = "数据已迁移到 %s（%d 项）" % (new_dir, len(moved))
        if errors:
            summary += "；部分失败：%s" % "；".join(errors)
        if not hook_ok:
            summary += "；键盘钩子重启失败，请以管理员身份重启程序"
        self.status_label.configure(text=summary)
        self._refresh()

    @staticmethod
    def _migrate_data(old_dir: str, new_dir: str):
        """把旧数据目录里的数据库 / 报告 / 溢出文件搬到新目录。"""
        os.makedirs(new_dir, exist_ok=True)
        moved, errors = [], []
        candidates = ["keylens.db", "keylens.db-wal", "keylens.db-shm"]
        try:
            candidates += [n for n in os.listdir(old_dir) if n.startswith("spill-")]
        except OSError:
            pass
        for name in candidates:
            src = os.path.join(old_dir, name)
            if not os.path.isfile(src):
                continue
            try:
                shutil.move(src, os.path.join(new_dir, name))
                moved.append(name)
            except Exception as exc:
                errors.append("%s（%s）" % (name, exc))
        reports = os.path.join(old_dir, "reports")
        if os.path.isdir(reports):
            try:
                shutil.move(reports, os.path.join(new_dir, "reports"))
                moved.append("reports\\")
            except Exception as exc:
                errors.append("reports（%s）" % exc)
        return moved, errors

    def _write_config_everywhere(self, old_dir: str) -> None:
        """把更新后的配置写到所有可能出现 config.json 的位置。

        程序可能从默认目录、旧数据目录或新数据目录任一处读到配置，
        三处都写同一份，避免"改了目录但下次启动没生效"。
        """
        import json
        from dataclasses import asdict

        payload = json.dumps(asdict(self.config), ensure_ascii=False, indent=2)
        folders = dict.fromkeys([old_dir, db_module.default_data_dir(), self.config.data_dir])
        for folder in folders:
            try:
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, "config.json"), "w", encoding="utf-8") as fh:
                    fh.write(payload)
            except Exception:
                pass

    def export_report(self) -> None:
        if self.report_module is None:
            messagebox.showinfo("报告", "报告模块未加载。")
            return
        try:
            path = self.report_module.write_daily_report(self.tracker.db, self.config)
        except Exception as exc:
            messagebox.showerror("生成报告失败", str(exc))
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            pass
        messagebox.showinfo("报告已生成", path)

    def clear_records(self) -> None:
        if self._closing:
            return
        answer = messagebox.askyesno(
            "清除记录",
            "将删除本机全部按键与剪贴板记录，此操作不可恢复。\n\n"
            "确定要继续吗？",
        )
        if not answer:
            return
        try:
            self.tracker.clear_records()
        except Exception as exc:
            messagebox.showerror("清除失败", str(exc))
            return
        messagebox.showinfo("已清除", "全部本地记录已删除，当前会话已重新开始。")
        # 时间线 / 剪贴板列表也要跟着清空（否则界面上还挂着已被删除的旧记录）
        self._tl_day = None
        self._tl_last_id = 0
        self._tl_procs = []
        self._cl_day = None
        self._cl_last_id = 0
        self._clip_procs = []
        try:
            self.timeline_list.delete(0, "end")
        except Exception:
            pass
        try:
            self.tl_filter.set(_TL_ALL)
            self.tl_filter_box.configure(values=[_TL_ALL])
            self.tl_day.set(_DAY_TODAY)
            self.tl_day_box.configure(values=[_DAY_TODAY])
            self._tl_days = []
            self.clip_filter.set(_TL_ALL)
            self.clip_filter_box.configure(values=[_TL_ALL])
            self.clip_day.set(_DAY_TODAY)
            self.clip_day_box.configure(values=[_DAY_TODAY])
            self._cl_days = []
        except Exception:
            pass
        try:
            self.clipboard_list.configure(state="normal")
            self.clipboard_list.delete("1.0", "end")
            self.clipboard_list.configure(state="disabled")
            self._clip_thumbs.clear()
        except Exception:
            pass
        self._refresh()

    def on_quit(self) -> None:
        if self._closing:
            return
        if not messagebox.askokcancel("退出 KeyLens", "停止记录并退出？"):
            return
        self._closing = True
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.tracker.set_key_observer(None)
        except Exception:
            pass
        if self._live_job is not None:
            try:
                self.root.after_cancel(self._live_job)
            except Exception:
                pass
            self._live_job = None
        try:
            self.tracker.stop()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


__all__ = ["KeyLensApp", "HeatmapCanvas"]
