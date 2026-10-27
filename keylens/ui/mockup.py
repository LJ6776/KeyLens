"""界面静态预览：用真实布局数据渲染一张 HTML，便于不开程序先看界面长什么样。

注意这是**离线渲染的界面样张**，不是运行截图；但它复用 :mod:`keylens.core.keymap`
的键位布局与 :mod:`keylens.ui.heat` 的配色函数，所以键位排布、格子上色规则与真实窗口一致。
真正的窗口截图请用 ``keylens/scripts/capture-window.ps1``。
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Dict, List, Tuple

from ..core import keymap
from . import theme as palette
from .heat import heat_color

# 一份确定性的示例数据：高频键（空格、E、T、A、O…）次数高，冷门键接近 0。
# 用固定表而不是随机数，保证每次预览长得一样，便于对比改动。
_SAMPLE_RANK: List[Tuple[str, int]] = [
    ("SPACE", 3200), ("E", 2100), ("T", 1750), ("A", 1620), ("O", 1480),
    ("I", 1410), ("N", 1330), ("S", 1290), ("R", 1180), ("H", 1120),
    ("L", 980), ("D", 950), ("BACKSPACE", 880), ("ENTER", 840), ("U", 790),
    ("C", 740), ("M", 700), ("F", 660), ("P", 620), ("W", 600),
    ("G", 560), ("Y", 520), ("B", 470), ("V", 440), ("K", 380),
    ("X", 300), ("J", 280), ("Q", 240), ("Z", 220),
    ("OEM_PERIOD", 210), ("OEM_COMMA", 190), ("OEM_1", 170), ("OEM_MINUS", 150),
    ("TAB", 300), ("DELETE", 260), ("UP", 240), ("DOWN", 230), ("LEFT", 250),
    ("RIGHT", 250), ("HOME", 90), ("END", 80), ("PAGEUP", 70), ("PAGEDOWN", 70),
    ("L_SHIFT", 900), ("R_SHIFT", 120), ("L_CTRL", 700), ("R_CTRL", 60),
    ("L_ALT", 260), ("R_ALT", 30), ("L_WIN", 40), ("CAPSLOCK", 25),
    ("OEM_3", 45), ("OEM_4", 60), ("OEM_5", 40), ("OEM_6", 60), ("OEM_7", 55),
    ("OEM_2", 30), ("ESC", 110), ("APPS", 15),
    ("F1", 12), ("F2", 20), ("F3", 8), ("F4", 30), ("F5", 90), ("F6", 10),
    ("F7", 6), ("F8", 14), ("F9", 5), ("F10", 9), ("F11", 11), ("F12", 18),
    ("NUMPAD0", 40), ("NUMPAD1", 60), ("NUMPAD2", 55), ("NUMPAD3", 50),
    ("NUMPAD4", 35), ("NUMPAD5", 30), ("NUMPAD6", 32), ("NUMPAD7", 20),
    ("NUMPAD8", 18), ("NUMPAD9", 16), ("NUMPAD_ADD", 70), ("NUMPAD_SUBTRACT", 65),
    ("NUMPAD_MULTIPLY", 12), ("NUMPAD_DIVIDE", 10), ("NUMPAD_DECIMAL", 22),
    ("NUMPAD_ENTER", 45), ("NUMLOCK", 6), ("PRINTSCREEN", 4), ("SCROLLLOCK", 2),
    ("INSERT", 18), ("PAUSE", 3),
]

_SAMPLE_APPS: List[Tuple[str, int]] = [
    ("chrome.exe", 5120), ("Code.exe", 3480), ("WINWORD.EXE", 2100),
    ("explorer.exe", 980), ("wechat.exe", 610), ("notepad.exe", 240),
]

_SAMPLE_SESSION = "37"


def sample_counts() -> Dict[str, int]:
    """示例键位次数，作为界面预览的数据源。"""
    return dict(_SAMPLE_RANK)


def sample_total() -> int:
    return sum(count for count in sample_counts().values())


def _fmt_int(value: int) -> str:
    return "{:,}".format(value)


def _render_block(rows: List[List[Tuple[str, float]]], counts: Dict[str, int], maximum: int,
                  title: str = "") -> str:
    parts: List[str] = []
    if title:
        parts.append('<div class="blocktitle">%s</div>' % html.escape(title))
    for row in rows:
        parts.append('<div class="row">')
        for item in row:
            if item is None:
                # 被跨行键占掉的空位，占一格宽度保持对齐
                parts.append('<div class="gap" style="flex:1"></div>')
                continue
            if not isinstance(item, tuple):
                # 指定宽度的空隙
                parts.append('<div class="gap" style="flex:%.2f"></div>' % float(item))
                continue
            key, width = item[0], item[1]
            value = counts.get(key, 0)
            parts.append(
                '<div class="key" style="flex:%.2f;background:%s">'
                '<span class="kl">%s</span><span class="kn">%s</span></div>'
                % (
                    width,
                    heat_color(value, maximum),
                    html.escape(keymap.display(key)),
                    value if value else "",
                )
            )
        parts.append("</div>")
    return "".join(parts)


def _render_metrics(counts: Dict[str, int]) -> str:
    total = sum(counts.values())
    specs = [
        (_fmt_int(total), "今日按键"),
        (_fmt_int(total), "本会话按键"),
        ("248", "最近 1 分钟"),
        ("1,204", "掩码按键"),
        ("05:12", "本会话时长"),
    ]
    return "".join(
        '<div class="card"><div class="v">%s</div><div class="c">%s</div></div>' % spec
        for spec in specs
    )


def _render_top_keys(counts: Dict[str, int]) -> str:
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:12]
    return " · ".join("%s %d" % (keymap.display(k), v) for k, v in ranked)


def _render_apps() -> str:
    return "<br>".join(
        "%s &nbsp; %s 次" % (html.escape(name), _fmt_int(count)) for name, count in _SAMPLE_APPS
    )


# 界面样张模板。占位符与 render_mockup() 的字典键一一对应，
# 少一个就会在运行时抛 KeyError（keylens.selftest 会覆盖到这条路径）。
_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>KeyLens 界面预览</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; background: %(bg)s; color: %(text)s;
  font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif; }
.hint { color: %(dim)s; font-size: 12px; margin-bottom: 16px; line-height: 1.7; }
.hint b { color: %(accent)s; }
.window { background: %(bg)s; border: 1px solid #2b3546; border-radius: 10px; overflow: hidden;
  box-shadow: 0 10px 30px rgba(0,0,0,.45); }
.titlebar { display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  background: #1a212c; border-bottom: 1px solid #2b3546; font-size: 12px; color: %(dim)s; }
.titlebar .dot { width: 9px; height: 9px; border-radius: 50%%; background: %(ok)s; }
.titlebar .win-title { color: %(text)s; }
.body { padding: 14px; }
.header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.header h1 { font-size: 16px; margin: 0; color: %(bright)s; font-weight: 700; }
.buttons { display: flex; gap: 6px; }
.btn { border: 1px solid #3b4759; background: #232b38; color: %(text)s; border-radius: 5px;
  padding: 6px 12px; font-size: 12px; }
.btn.primary { border-color: %(accent)s; color: %(accent)s; }
.status { margin-top: 10px; font-size: 12px; color: %(dim)s; }
.warn { margin-top: 10px; background: %(warnbg)s; color: %(warntext)s; border-radius: 5px;
  padding: 6px 10px; font-size: 12px; }
.cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 12px 0 16px; }
.card { background: %(panel)s; border-radius: 8px; padding: 10px 12px; }
.card .v { font-size: 22px; font-weight: 700; color: %(accent)s; }
.card .c { font-size: 11px; color: %(dim)s; margin-top: 2px; }
.maps { display: grid; grid-template-columns: 5fr 2fr; gap: 10px; }
.kb { background: %(panel)s; border-radius: 8px; padding: 10px; overflow-x: auto; }
.blocktitle { font-size: 10px; color: %(dim)s; margin: 8px 0 5px; }
.row { display: flex; gap: 4px; margin-bottom: 4px; min-width: 0; }
.key { height: 40px; border: 1px solid %(border)s; border-radius: 5px; display: flex;
  flex-direction: column; align-items: center; justify-content: center; font-size: 9px;
  color: %(bright)s; overflow: hidden; }
.key .kn { font-size: 10px; font-weight: 700; }
.bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
.panel { background: %(panel)s; border-radius: 8px; padding: 10px 12px; font-size: 12px; }
.panel h2 { font-size: 12px; margin: 0 0 6px; color: %(text)s; font-weight: 600; }
.panel .muted { color: %(dim)s; }
</style></head><body>

<div class="hint">
  <b>这是界面样张，不是运行截图。</b>由 <code>keylens.ui.mockup</code> 离线渲染，
  键位布局取自 <code>keylens.core.keymap</code>，格子配色取自 <code>keylens.ui.heat</code>，
  与真实窗口同一套规则；真实窗口的字体、边距会略有差异。<br>
  数据为固定示例值（空格 3200 次最高），生成时间 %(generated)s。
</div>

<div class="window">
  <div class="titlebar">
    <span class="dot"></span>
    <span class="win-title">KeyLens 按键记录器 · 正在记录 · 今日 %(total)s 次</span>
    <span>—</span><span>□</span><span>×</span>
  </div>
  <div class="body">
    <div class="header">
      <h1>KeyLens · 按键记录器（本机自用）</h1>
      <div class="buttons">
        <span class="btn">暂停记录</span>
        <span class="btn">生成报告</span>
        <span class="btn">打开数据目录</span>
        <span class="btn">退出</span>
      </div>
    </div>
    <div class="status">
      状态：正在记录（运行中）｜ 会话 #%(sessions)s ｜ 远程会话中
      ｜ 隐私掩码：开启 ｜ 证据：向日葵远程控制 - 会话窗口 ｜ 丢弃事件：0
    </div>
    %(banner)s
    <div class="cards">%(cards)s</div>
    <div class="maps">
      <div class="kb">%(keyboard_main)s</div>
      <div class="kb">%(keyboard_side)s</div>
    </div>
    <div class="bottom">
      <div class="panel"><h2>最常按的键（今日）</h2><span class="muted">%(top_keys)s</span></div>
      <div class="panel"><h2>按键分布（按前台应用，今日）</h2><span class="muted">%(apps)s</span></div>
    </div>
  </div>
</div>
</body></html>"""


def render_mockup(masking_now: bool = True) -> str:
    """返回界面预览页的完整 HTML。"""
    counts = sample_counts()
    total = sum(counts.values())
    maximum = max(list(counts.values()) + [1])
    banner = (
        '<div class="warn">当前前台应用命中隐私掩码：只统计按键次数，不记录具体键位。</div>'
        if masking_now
        else ""
    )
    return _TPL % {
        "bg": palette.BG,
        "panel": palette.BG_PANEL,
        "text": palette.TEXT,
        "dim": palette.TEXT_DIM,
        "bright": palette.TEXT_BRIGHT,
        "accent": palette.ACCENT,
        "ok": palette.OK,
        "border": palette.KEY_BORDER,
        "warnbg": palette.WARN_BG,
        "warntext": palette.WARN_TEXT,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": _fmt_int(total),
        "sessions": _SAMPLE_SESSION,
        "banner": banner,
        "cards": _render_metrics(counts),
        "keyboard_main": (
            _render_block([keymap.FUNCTION_ROW], counts, maximum, title="功能键区")
            + _render_block(keymap.KEYBOARD_ROWS, counts, maximum, title="主键盘区")
        ),
        "keyboard_side": (
            _render_block(keymap.NAV_ROWS, counts, maximum, title="控制键区")
            + _render_block(keymap.NUMPAD_ROWS, counts, maximum, title="数字键区")
        ),
        "top_keys": _render_top_keys(counts),
        "apps": _render_apps(),
    }


__all__ = ["render_mockup", "sample_counts", "sample_total", "_TPL"]
