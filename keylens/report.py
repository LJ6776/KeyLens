"""HTML 报告：把一天的按键统计渲染成一张可直接双击打开的单文件网页。

报告刻意不含任何"输入内容"，只有键位次数、分类占比、应用分布、分时曲线，
以及本地/远程会话的对照，方便回答"这段时间的输入是谁敲的、敲了多少"。
"""

from __future__ import annotations

import base64
import html
import json
import os
import struct
import zlib
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .core import keymap, winapi
from .core.db import Database, DaySummary

if TYPE_CHECKING:  # 仅供类型注解使用，运行时不导入（避免把 winapi / clipboard 拖进来）
    from .core.tracker import Config

try:  # 剪贴板显示名是 Win32 模块里的，取不到就退回原样显示，不影响报告生成
    from .core import clipboard as clipboard_module
except Exception:  # pragma: no cover
    clipboard_module = None  # type: ignore[assignment]

# 与界面主题保持一致的配色
_BG = "#12161d"
_PANEL = "#1b212b"
_TEXT = "#d7dee8"
_DIM = "#8b97a8"
_ACCENT = "#4aa3ff"
_HEAT_LOW = (46, 88, 140)
_HEAT_MID = (58, 150, 190)
_HEAT_HIGH = (255, 138, 76)

_STYLE = """
/* 双主题：全部颜色走变量。暗色为默认；:root[data-theme="light"] 覆盖成明亮配色。
   顶栏内容与正文共用同一个 max-width 容器（.topbar-inner），标题和日期不再贴屏幕两边。 */
:root {
  color-scheme: dark;
  --bg: #0f131a; --bg-input: #12161d; --panel: #171d27; --panel-2: #1a212e;
  --border: #29344a; --border-soft: #222b39; --border-strong: #3b485d;
  --text: #dae2ee; --text-strong: #e8eef7; --dim: #8d99ac; --dim2: #aab6c6; --dim3: #6f7c8f;
  --accent: #4d9fff; --accent-2: #2f6db8; --ok: #41d495; --warn: #ffcf70; --violet: #9d7bff;
  --topbar-bg: rgba(15,19,26,.92); --shadow: rgba(0,0,0,.55);
  --sel: rgba(77,159,255,.35); --hover-row: rgba(77,159,255,.06); --row-stripe: rgba(255,255,255,.02);
  --badge-r-border: #5a4a1a; --badge-r-bg: #2a2208; --badge-l-border: #1e4a38; --badge-l-bg: #0d2419;
  --foot-bg: #141b26;
}
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #eef1f6; --bg-input: #ffffff; --panel: #ffffff; --panel-2: #f2f6fb;
  --border: #ccd6e2; --border-soft: #dce4ee; --border-strong: #b7c4d4;
  --text: #2b3542; --text-strong: #101826; --dim: #5d6875; --dim2: #46505c; --dim3: #97a2b0;
  --accent: #2e7fd9; --accent-2: #1c5c9e; --ok: #128a5a; --warn: #8a5c00; --violet: #6d4fd0;
  --topbar-bg: rgba(238,241,246,.92); --shadow: rgba(30,45,70,.16);
  --sel: rgba(46,127,217,.22); --hover-row: rgba(46,127,217,.07); --row-stripe: rgba(15,35,66,.03);
  --badge-r-border: #e3cd94; --badge-r-bg: #fff7e2; --badge-l-border: #bfe6d4; --badge-l-bg: #e9f8f0;
  --foot-bg: #f6f9fd;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6;
  font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
  transition: background .18s ease, color .18s ease; }
.topbar { position: sticky; top: 0; z-index: 10;
  background: var(--topbar-bg); backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border); }
.topbar-inner { max-width: 1400px; margin: 0 auto; padding: 11px 24px;
  display: flex; align-items: center; gap: 10px; }
.topbar .logo { flex: none; width: 22px; height: 22px; border-radius: 6px; overflow: hidden;
  background: linear-gradient(135deg, var(--accent), var(--accent-2)); }
.topbar .logo img { display: block; width: 100%%; height: 100%%; }
.topbar .t { font-weight: 700; font-size: 15px; }
.topbar .day { margin-left: auto; font-size: 12px; color: var(--dim);
  font-variant-numeric: tabular-nums; }
/* 亮暗切换：显示的是"点一下会切到的模式" */
.theme-btn { flex: none; display: inline-flex; align-items: center; justify-content: center;
  margin-left: 14px; padding: 4px 12px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--panel); color: var(--dim);
  font-size: 12.5px; font-family: inherit; cursor: pointer; line-height: 1.5;
  transition: color .15s ease, border-color .15s ease; }
.theme-btn:hover { color: var(--text); border-color: var(--border-strong); }
main { max-width: 1400px; margin: 0 auto; padding: 26px 24px 48px; }
h1 { font-size: 24px; margin: 6px 0 4px; font-weight: 800; letter-spacing: .3px; }
h2 { font-size: 17px; margin: 36px 0 14px; padding-left: 11px; position: relative; font-weight: 700; }
h2::before { content: ""; position: absolute; left: 0; top: 3px; bottom: 3px; width: 3px;
  border-radius: 2px; background: linear-gradient(180deg, var(--accent), var(--accent-2)); }
.meta { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 26px; }
.chip { font-size: 12px; color: var(--dim2); background: var(--panel); border: 1px solid var(--border);
  border-radius: 999px; padding: 3px 12px; }
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(172px, 1fr)); }
.card { position: relative; overflow: hidden; background: var(--panel); border: 1px solid var(--border);
  border-radius: 12px; padding: 14px 16px 12px;
  transition: transform .15s ease, border-color .15s ease, box-shadow .15s ease; }
.card:hover { transform: translateY(-2px); border-color: var(--border-strong);
  box-shadow: 0 8px 22px var(--shadow); }
.card::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
  background: var(--c, var(--accent)); opacity: .9; }
.card .v { font-size: 26px; font-weight: 800; color: var(--c, var(--accent)); line-height: 1.2;
  font-variant-numeric: tabular-nums; }
.card .c { font-size: 12px; color: var(--dim); margin-top: 4px; }
.c-blue { --c: var(--accent); } .c-green { --c: var(--ok); } .c-amber { --c: var(--warn); }
.c-violet { --c: var(--violet); }
.kb { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px;
  overflow-x: auto; }
.kblayout { display: flex; gap: 12px; align-items: flex-start; min-width: 860px; }
.kbcol { min-width: 0; display: flex; flex-direction: column; }
.kbgrid { display: grid; grid-template-columns: repeat(4, 1fr); grid-auto-rows: 56px;
  gap: 4px; width: 100%%; }
.kbgrid .key { height: 100%%; }
.blocktitle { font-size: 12px; color: var(--dim); margin: 12px 0 6px; font-weight: 600; }
.row { display: flex; gap: 4px; margin-bottom: 4px; }
.key { position: relative; height: 46px; border: 1px solid var(--border-strong); border-radius: 6px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  font-size: 10px; color: #ffffff; text-shadow: 0 1px 2px rgba(0,0,0,.35); overflow: hidden;
  transition: transform .09s ease, filter .09s ease, box-shadow .09s ease; }
.key.pressing { transform: translateY(3px) scale(.95); filter: brightness(1.45) saturate(1.3);
  box-shadow: 0 0 0 2px #ff9a5c inset, 0 6px 14px rgba(255,154,92,.25); z-index: 2; }
.playbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 10px;
  padding: 10px 12px; background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  font-size: 12.5px; color: var(--dim2); }
.playbar select { background: var(--bg-input); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 4px 8px; font-size: 12.5px; }
.playbar input { background: var(--bg-input); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 3px 6px; font-size: 12.5px; }
.playbar label { display: inline-flex; align-items: center; gap: 5px; }
.playbar .pb-play { background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #ffffff;
  border: none; border-radius: 6px; padding: 5px 14px; font-size: 12.5px; cursor: pointer; }
.playbar .pb-play:hover { filter: brightness(1.15); }
.playbar .pb-restart { background: var(--panel-2); color: var(--text); border: 1px solid var(--border-strong);
  border-radius: 6px; padding: 5px 12px; font-size: 12.5px; cursor: pointer; }
.playbar .pb-restart:hover { border-color: var(--accent); color: var(--text-strong); }
.playbar .pb-info { color: var(--dim); font-variant-numeric: tabular-nums; }
.livetip { display: flex; align-items: stretch; gap: 12px; margin-top: 12px; min-height: 66px;
  padding: 12px 16px; background: var(--panel); border: 1px solid var(--border); border-radius: 12px; }
.livetip .lt-label { flex: none; align-self: center; font-size: 12px; color: var(--dim);
  border: 1px solid var(--border); border-radius: 999px; padding: 3px 10px; }
/* 显示区吃掉剩余的全部宽度：左侧标签之后一直延伸到右侧时间/程序信息之前 */
.livetip .lt-main { flex: 1 1 auto; min-width: 0; position: relative; overflow: hidden;
  background: var(--bg-input); border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px;
  display: flex; align-items: center; cursor: text; user-select: text; -webkit-user-select: text; }
.livetip .lt-idle { font-size: 15px; font-weight: 500; color: var(--dim); }
.livetip .lt-track { display: inline-block; white-space: nowrap; will-change: transform; }
.livetip .lt-chip { display: inline-block; margin-right: 20px; font-size: 24px; font-weight: 700;
  color: var(--text-strong); line-height: 1.5; cursor: text; user-select: text; -webkit-user-select: text; }
.livetip .lt-chip.clip { font-size: 17px; color: var(--warn); }
.livetip .lt-chip img.lt-thumb { display: inline-block; height: 38px; max-width: 160px; width: auto;
  border: 1px solid var(--border-strong); border-radius: 4px; vertical-align: middle; }
.livetip .lt-chip .cap { font-size: 13px; font-weight: 500; color: var(--warn); margin-left: 6px;
  vertical-align: middle; }
/* 图片拿不到内容时的占位缩略图：纯 CSS，不依赖图片解码 */
.livetip .lt-thumb.ph { display: inline-flex; align-items: center; justify-content: center;
  height: 38px; width: 96px; border: 1px dashed var(--accent); border-radius: 4px;
  color: var(--dim); font-size: 12px; font-weight: 600; vertical-align: middle; }
.livetip .lt-chip .t { font-size: 12px; font-weight: 500; color: var(--dim3); margin-left: 6px; }
.livetip ::selection { background: var(--sel); }
/* 图片放大预览（灯箱）与右键菜单 */
.kb-lightbox { position: fixed; inset: 0; z-index: 999; display: none; cursor: zoom-out;
  background: rgba(8,11,16,.88); }
.kb-lightbox.on { display: flex; align-items: center; justify-content: center; }
.kb-lightbox img { max-width: 88vw; max-height: 86vh; border: 1px solid var(--border-strong); border-radius: 8px;
  box-shadow: 0 24px 70px rgba(0,0,0,.65); }
.kb-lightbox .lb-cap { position: absolute; left: 0; right: 0; bottom: 26px; text-align: center;
  color: #e8eef7; font-size: 12.5px; line-height: 1.7; }
.kb-menu { position: fixed; z-index: 1000; min-width: 178px; padding: 4px; font-size: 12.5px;
  background: var(--panel-2); border: 1px solid var(--border-strong); border-radius: 8px;
  box-shadow: 0 14px 34px var(--shadow); }
.kb-menu div { padding: 7px 12px; border-radius: 5px; color: var(--text); cursor: pointer;
  white-space: nowrap; }
.kb-menu div:hover { background: var(--hover-row); }
.kb-menu div.off { color: var(--dim3); cursor: default; }
.kb-menu div.off:hover { background: none; }
.livetip .lt-sub { flex: none; width: 250px; align-self: center; text-align: right;
  font-size: 12px; color: var(--dim); line-height: 1.6; }
.key .n { font-size: 11px; font-weight: 700; }
.gap { visibility: hidden; }
.legend { display: flex; align-items: center; gap: 8px; margin: 12px 2px 0;
  font-size: 11px; color: var(--dim); }
.legend .lg { width: 150px; height: 8px; border-radius: 99px; border: 1px solid var(--border); }
.hours { display: flex; align-items: flex-end; gap: 3px; height: 160px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 12px 8px; }
.hour { flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
  align-items: center; height: 100%%; font-size: 9px; color: var(--dim); }
.hour .bar { width: 100%%; background: linear-gradient(180deg, var(--accent), var(--accent-2));
  border-radius: 3px 3px 0 0; min-height: 1px; transition: filter .15s ease; }
.hour .bar:hover { filter: brightness(1.3); }
.hour .bar.peak { background: linear-gradient(180deg, #ff9a5c, #ff6b6b); }
.tbl { border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: var(--panel); }
table { border-collapse: collapse; width: 100%%; }
th, td { text-align: left; padding: 9px 14px; font-size: 13px; border-bottom: 1px solid var(--border-soft); }
th { color: var(--dim); font-weight: 600; background: var(--panel-2); }
tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: var(--hover-row); }
.badge { display: inline-block; padding: 1px 10px; border-radius: 999px; font-size: 12px;
  border: 1px solid; white-space: nowrap; }
.badge.remote { color: var(--warn); border-color: var(--badge-r-border); background: var(--badge-r-bg); }
.badge.local { color: var(--ok); border-color: var(--badge-l-border); background: var(--badge-l-bg); }
.bar { width: 110px; height: 6px; background: var(--border-soft); border-radius: 99px;
  display: inline-block; vertical-align: middle; margin-right: 8px; overflow: hidden; }
.bar span { display: block; height: 100%%; border-radius: 99px;
  background: linear-gradient(90deg, var(--accent-2), var(--accent)); }
.rank { color: var(--dim); font-variant-numeric: tabular-nums; }
.timeline-wrap { border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.timeline-body { max-height: 600px; overflow-y: auto; }
.timeline-body tr:nth-child(even) td { background: var(--row-stripe); }
.filterbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 10px 12px; background: var(--panel-2); border: 1px solid var(--border); border-radius: 12px;
  margin-bottom: 10px; font-size: 12.5px; color: var(--dim2); }
.filterbar select, .filterbar input { background: var(--bg-input); color: var(--text);
  border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 12.5px; }
.filterbar select { max-width: 260px; }
.filterbar label { display: inline-flex; align-items: center; gap: 5px; }
.filterbar .f-clear { background: var(--panel); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 12.5px; }
.filterbar .f-clear:hover { border-color: var(--border-strong); }
.filterbar .f-count { margin-left: auto; color: var(--dim); font-variant-numeric: tabular-nums; }
.tc { text-align: center; }
.thumbcell { display: inline-block; }
.thumbcell img { max-height: 34px; max-width: 120px; border: 1px solid var(--border-strong);
  border-radius: 4px; vertical-align: middle; }
.thumbph { display: inline-flex; align-items: center; justify-content: center; height: 34px;
  min-width: 78px; padding: 0 10px; border: 1px dashed var(--accent); border-radius: 4px;
  color: var(--dim); font-size: 12px; }
.empty { color: var(--dim); font-size: 13px; padding: 10px 2px; }
.foot { margin-top: 36px; padding: 18px 20px; background: var(--foot-bg); border: 1px solid var(--border);
  border-left: 3px solid var(--accent); border-radius: 12px; font-size: 12.5px; color: var(--dim2);
  line-height: 1.9; }
.foot b { color: var(--text); }
@media print { .topbar { position: static; } .card:hover { transform: none; } .theme-btn { display: none; } }
""" % {"bg": _BG, "panel": _PANEL, "text": _TEXT, "dim": _DIM, "accent": _ACCENT}

# 日报页面模板。占位符必须是普通 %(name)s 形式，且与 build_html() 里的字典键一一对应。
# 新增占位符时务必同时加字典项：少一个就会在运行时抛 TypeError（见 keylens.selftest）。
_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KeyLens 报告 %(day)s</title><style>%(style)s</style></head><body>
<div class="topbar"><div class="topbar-inner"><span class="logo">%(logo)s</span><span class="t">KeyLens 按键统计报告</span>
  <span class="day">%(day)s%(suffix)s</span>
  <button type="button" class="theme-btn" id="themeBtn"><span id="themeBtnText">明亮</span></button></div></div>
<main>
<h1>按键统计 · %(day)s%(suffix)s</h1>
<div class="meta">
  <span class="chip">生成时间 %(generated)s</span>
  <span class="chip">数据文件 %(db)s</span>
  <span class="chip">仅供本机自查使用</span>
</div>
<div class="grid">
  <div class="card c-blue"><div class="v">%(total)s</div><div class="c">当日按键总数</div></div>
  <div class="card c-blue"><div class="v">%(kpm).1f</div><div class="c">平均每分钟按键</div></div>
  <div class="card c-violet"><div class="v">%(active)s</div><div class="c">当日活跃跨度（首末按键之间）</div></div>
  <div class="card c-blue"><div class="v">%(session_count)d</div><div class="c">当日记录会话数</div></div>
  <div class="card c-amber"><div class="v">%(masked)s</div><div class="c">掩码按键（未记录具体键位）</div></div>
  <div class="card c-green"><div class="v">%(coverage).0f%%</div><div class="c">键盘覆盖率</div></div>
  <div class="card c-amber"><div class="v">%(remote)s</div><div class="c">远程会话内按键</div></div>
  <div class="card c-green"><div class="v">%(local)s</div><div class="c">本地键盘按键</div></div>
  <div class="card c-green"><div class="v">%(clip_copy)s</div><div class="c">复制次数（剪贴板内容变化）</div></div>
  <div class="card c-green"><div class="v">%(clip_paste)s</div><div class="c">粘贴次数（Ctrl+V 等）</div></div>
</div>

<h2 id="heatmap">键盘热力图</h2>
%(playback)s<div class="kb">%(heatmap)s%(legend)s</div>
%(livetip)s

<h2 id="hours">分时分布（每小时按键次数）</h2>
%(hours)s

<h2 id="topkeys">键位排行</h2>
%(top_keys)s

<h2 id="categories">按键类别占比</h2>
%(categories)s

<h2 id="procs">按键分布（按前台应用）</h2>
%(top_procs)s

<h2 id="remote">远程控制 / 监控软件输入</h2>
%(remote_apps)s

<h2 id="clipboard">复制 / 粘贴</h2>
%(clipboard)s

<h2 id="sessions">会话明细（本地 / 远程）</h2>
%(sessions)s

<h2 id="timeline">按键时间线（最近 7 天，非掩码，可按日期 / 时间 / 程序筛选）</h2>
%(timeline)s

<div class="foot">
  <b>这份报告记录了什么：</b>只有"哪个键被按下、什么时候、当时前台是哪个程序"。<br>
  <b>没有记录什么：</b>不产生任何字符内容，也不提供把按键序列还原成文字的功能；
  命中隐私掩码的应用（密码管理器、浏览器等）只累计次数，键位一律归入掩码桶。<br>
  <b>数据在哪：</b>%(data_dir)s，全部为本地文件，程序不含任何网络代码。<br>
  <b>远程会话：</b>通过检测远程控制软件（向日葵 / ToDesk / TeamViewer / VNC / RustDesk 等）的窗口与进程判定。
  远程注入的按键与本地按键在系统中同样走输入队列，因此都会被记录，并单独标记供对照；
  各远程控制软件的具体输入量见「远程控制 / 监控软件输入」小节。<br>
  <b>复制 / 粘贴：</b>复制由剪贴板内容变化触发，粘贴由 Ctrl+V / Shift+Insert 判定；
  只登记动作、内容类型（文本 / 图片 / 文件）与字符数，默认不保存剪贴板正文；
  命中隐私掩码时连类型与长度都不记。
</div>
</main>
%(theme_js)s
<script>
/* 明细表筛选：每条记录带 data-day（年月日）、data-sec（当天秒数）与 data-proc（前台程序），
   筛选条提供日期下拉、程序下拉与起止时间，全部在本地完成，不依赖任何外部资源。 */
(function () {
  function toSec(v) {
    if (!v) return null;
    var p = v.split(":");
    return (parseInt(p[0], 10) || 0) * 3600 + (parseInt(p[1], 10) || 0) * 60 + (parseInt(p[2], 10) || 0);
  }
  function endSec(v) {
    var s = toSec(v);
    if (s === null) return null;
    return v.split(":").length > 2 ? s : s + 59; // 只选到分钟时含该分钟末尾
  }
  document.querySelectorAll(".filterbar").forEach(function (bar) {
    var tbody = document.getElementById(bar.getAttribute("data-target"));
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
    var daySel = bar.querySelector(".f-day");
    var procSel = bar.querySelector(".f-proc");
    var fromInp = bar.querySelector(".f-from");
    var toInp = bar.querySelector(".f-to");
    var countEl = bar.querySelector(".f-count");
    function apply() {
      var day = daySel ? daySel.value : "";
      var proc = procSel ? procSel.value : "";
      var from = toSec(fromInp ? fromInp.value : "");
      var to = endSec(toInp ? toInp.value : "");
      var shown = 0;
      rows.forEach(function (tr) {
        var sec = parseInt(tr.getAttribute("data-sec"), 10) || 0;
        var ok = (!day || tr.getAttribute("data-day") === day)
          && (!proc || tr.getAttribute("data-proc") === proc)
          && (from === null || sec >= from)
          && (to === null || sec <= to);
        tr.style.display = ok ? "" : "none";
        if (ok) shown++;
      });
      if (countEl) countEl.textContent = shown + " / " + rows.length + " 条";
    }
    bar.addEventListener("input", apply);
    bar.addEventListener("change", apply);
    var reset = bar.querySelector(".f-clear");
    if (reset) reset.addEventListener("click", function () {
      if (daySel) daySel.value = "";
      if (procSel) procSel.value = "";
      if (fromInp) fromInp.value = "";
      if (toInp) toInp.value = "";
      apply();
    });
    apply();
  });

  /* 模拟键盘回放：数据来自页面内嵌的按键事件序列（与下方时间线表无关），
     按时间顺序在这把全键盘上逐个点亮键位，日期 / 程序 / 节奏都在这条控制条里选。 */
  var playBtn = document.querySelector(".pb-play");
  var restartBtn = document.querySelector(".pb-restart");
  var layout = document.querySelector(".kblayout");
  var speedSel = document.querySelector(".pb-speed");
  var daySel = document.querySelector(".pb-day");
  var procSel = document.querySelector(".pb-proc");
  var fromInp = document.querySelector(".pb-from");
  var toInp = document.querySelector(".pb-to");
  var pbInfo = document.querySelector(".pb-info");
  var ltView = document.getElementById("kb-live-main");
  var ltTrack = document.getElementById("kb-live-track");
  var ltIdle = document.getElementById("kb-live-idle");
    var lb = document.getElementById("kb-lightbox");
    var lbImg = document.getElementById("kb-lightbox-img");
    var lbCap = document.getElementById("kb-lightbox-cap");
    function status(msg) { if (pbInfo) pbInfo.textContent = msg; }
    /* -- 图片：放大预览 / 右键复制 / 复制路径 -- */
    function openPreview(src, cap) {
      if (!lb || !src || src === "placeholder") return;
      lbImg.src = src;
      lbCap.textContent = cap || "";
      lb.classList.add("on");
    }
    function closePreview() {
      if (!lb) return;
      lb.classList.remove("on");
      lbImg.src = "";
    }
    if (lb) {
      lb.addEventListener("click", closePreview);
      document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePreview(); });
    }
    function copyText(text) {
      if (!text) return false;
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      document.body.removeChild(ta);
      return ok;
    }
    function copyImage(src, path) {
      // 报告是本地文件（file://），部分浏览器不允许直接写图片到剪贴板；
      // 失败时退回到"复制图片路径"，保证用户至少能拿到图。
      try {
        if (!navigator.clipboard || !window.ClipboardItem) throw new Error("no api");
        fetch(src).then(function (r) { return r.blob(); }).then(function (blob) {
          return navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        }).then(function () {
          status("已复制图片到剪贴板");
        }).catch(function () {
          copyText(path);
          status(path ? "浏览器不允许复制图片，已改为复制图片路径" : "浏览器不允许复制图片");
        });
      } catch (err) {
        copyText(path);
        status(path ? "浏览器不允许复制图片，已改为复制图片路径" : "浏览器不允许复制图片");
      }
    }
    var menuEl = null;
    function hideMenu() {
      if (menuEl) { menuEl.remove(); menuEl = null; }
    }
    document.addEventListener("click", hideMenu);
    document.addEventListener("scroll", hideMenu, true);
    function showMenu(x, y, src, path, cap) {
      hideMenu();
      var menu = document.createElement("div");
      menu.className = "kb-menu";
      var items = [
        ["放大预览", src && src !== "placeholder", function () { openPreview(src, cap); }],
        ["复制图片到剪贴板", src && src !== "placeholder", function () { copyImage(src, path); }],
        ["复制图片路径", !!path, function () { copyText(path); status("已复制图片路径"); }],
      ];
      items.forEach(function (item) {
        var el = document.createElement("div");
        el.textContent = item[0];
        if (!item[1]) el.className = "off";
        el.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (item[1]) item[2]();
          hideMenu();
        });
        menu.appendChild(el);
      });
      document.body.appendChild(menu);
      menu.style.left = Math.max(6, Math.min(x, window.innerWidth - 190)) + "px";
      menu.style.top = Math.max(6, Math.min(y, window.innerHeight - 120)) + "px";
      menuEl = menu;
    }
  var dataEl = document.getElementById("kb-events");
  if (playBtn && layout && dataEl) {
    var events = [];
    try { events = JSON.parse(dataEl.textContent || "[]"); } catch (err) { events = []; }
    var timer = null;
    var playing = false;
    function pick() {
      var day = daySel ? daySel.value : "";
      var proc = procSel ? procSel.value : "";
      var from = toSec(fromInp ? fromInp.value : "");
      var to = endSec(toInp ? toInp.value : "");
      return events.filter(function (e) {
        return (!day || e[0] === day)
          && (!proc || e[3] === proc)
          && (from === null || e[1] >= from)
          && (to === null || e[1] <= to);
      });
    }
    function refreshCount() {
      var n = pick().length;
      if (pbInfo) pbInfo.textContent = "符合筛选：" + n + " / " + events.length + " 次事件可回放";
    }
    var current = null;
    /* 实时按键走马灯：文字的横向移动完全由"按键事件"驱动——
       每来一个按键才滑一次，滑动时长就是这次按键的间隔（ ease-out ），
       所以键盘上键位的闪烁节奏与下方文字的移动节奏始终是同一条时间轴。 */
    var ltX = 0, ltRaf = null, ltFrom = 0, ltTo = 0, ltT0 = 0, ltDur = 1;
    function ltTarget() {
      if (!ltView || !ltTrack) return 0;
      var over = ltTrack.scrollWidth - (ltView.clientWidth - 24);
      return over > 0 ? -over : 0;
    }
    function ltTick(ts) {
      if (!ltView || !ltTrack) return;
      var p = ltDur > 0 ? Math.min(1, (ts - ltT0) / ltDur) : 1;
      var eased = 1 - Math.pow(1 - p, 2); // ease-out：起步快、收尾稳
      ltX = ltFrom + (ltTo - ltFrom) * eased;
      var first = ltTrack.firstElementChild;
      // 完全滑出左边的片段直接移除，队列不会无限增长
      if (first && first.offsetLeft + first.offsetWidth + ltX < -4) {
        ltX += first.offsetWidth + 20;
        ltTrack.removeChild(first);
      }
      ltTrack.style.transform = "translateX(" + ltX.toFixed(1) + "px)";
      if (p >= 1) { ltRaf = null; return; }
      ltRaf = window.requestAnimationFrame(ltTick);
    }
    function ltSlide(durationMs) {
      ltFrom = ltX;
      ltTo = ltTarget();
      ltDur = Math.max(60, durationMs || 0);
      ltT0 = (window.performance && performance.now) ? performance.now() : Date.now();
      if (ltRaf === null) ltRaf = window.requestAnimationFrame(ltTick);
    }
    function ltStop() {
      if (ltRaf !== null) { window.cancelAnimationFrame(ltRaf); ltRaf = null; }
    }
    /* 只有"重新开始 / 换筛选条件"才清空走马灯；单纯暂停时保留已显示的内容 */
    function ltClear() {
      ltStop();
      ltX = 0; ltFrom = 0; ltTo = 0;
      if (ltTrack) {
        ltTrack.innerHTML = "";
        ltTrack.style.transform = "translateX(0)";
      }
      if (ltIdle) ltIdle.style.display = "";
    }
    function ltPush(text, when, isClip, media, path, durationMs) {
      if (!ltTrack) return;
      if (ltIdle) ltIdle.style.display = "none";
      var chip = document.createElement("span");
      chip.className = "lt-chip" + (isClip ? " clip" : "");
      // 时间不再常驻显示，鼠标悬停时用 title 提示；图片再附上原始文件路径
      chip.title = (when || "") + (path ? "\\n路径：" + path : "");
      if (media && media !== "placeholder") {
        var img = document.createElement("img");
        img.className = "lt-thumb";
        img.alt = text || "剪贴板图片";
        img.src = media;
        chip.appendChild(img);
        var cap = document.createElement("span");
        cap.className = "cap";
        cap.textContent = text || "图片";
        chip.appendChild(cap);
        // 点击放大预览，右键弹出复制菜单
        chip.style.cursor = "zoom-in";
        chip.addEventListener("click", function () { openPreview(media, chip.title); });
        chip.addEventListener("contextmenu", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          showMenu(ev.clientX, ev.clientY, media, path || "", chip.title);
        });
      } else if (media === "placeholder") {
        // 图片拿不到内容时，用纯 CSS 画一个缩略图块，保证有图可看
        var ph = document.createElement("span");
        ph.className = "lt-thumb ph";
        ph.textContent = "图片";
        chip.appendChild(ph);
        chip.addEventListener("contextmenu", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          showMenu(ev.clientX, ev.clientY, "", path || "", chip.title);
        });
      } else {
        chip.textContent = text;
      }
      ltTrack.appendChild(chip);
      ltSlide(durationMs); // 用这次的按键间隔去滑动，文字与键盘节奏完全一致
    }
    function clearMarks() {
      if (current) { current.classList.remove("pressing"); current = null; }
      layout.querySelectorAll(".key.pressing").forEach(function (k) {
        k.classList.remove("pressing");
      });
    }
    function stop(keep) {
      playing = false;
      if (timer) { clearTimeout(timer); timer = null; }
      playBtn.textContent = "\u25B6 回放";
      clearMarks();
      ltStop();           // 滚动也一并停下：暂停时键盘不闪、文字也不滑，节奏保持一致
      if (!keep) ltClear();
    }
    function gapMs(gapSec) {
      // 倍速：真实间隔 / 倍数。必须先把原始间隔截断到 0.08~3 秒、再除倍数——
      // 之前是先除再夹进 [30,1200]ms：一天几千个事件的平均间隔有几十秒，
      // 无论几倍速全被 1200ms 上限压平，倍速选项形同虚设。
      // 截断的含义：超过 3 秒的停顿（离开键盘）按 3 秒封顶，节奏感保留。
      var mult = speedSel ? (parseFloat(speedSel.value) || 1) : 1;
      var sec = Math.min(3, Math.max(0.08, gapSec));
      return Math.max(20, sec * 1000 / mult);
    }
    function step(list, i, prev) {
      try {
        stepInner(list, i, prev);
      } catch (err) {
        // 任何异常都要显示出来，绝不让回放"卡住不动"
        if (pbInfo) pbInfo.textContent = "回放出错：" + (err && err.message ? err.message : err);
        stop(true);
      }
    }
    function stepInner(list, i, prev) {
      if (!playing) return;
      if (i >= list.length) {
        if (pbInfo) pbInfo.textContent = "回放完成，共 " + list.length + " 次按键（点「重新开始」可从头再放一次）";
        stop(true);  // 放完保留已经显示的内容，方便回看
        return;
      }
      var ev = list[i];
      var gap = (prev && prev[0] === ev[0]) ? (ev[1] - prev[1]) : 0.1;
      var wait = gapMs(gap);
      // 同一时刻只点亮一个键：先熄灭上一个，再点亮当前。
      // 否则高倍速下间隔只有几十毫秒，会出现好几个键同时处于"按下"状态。
      clearMarks();
      var isClip = ev[4] === "copy" || ev[4] === "paste";
      var cell = (!isClip && ev[5]) ? layout.querySelector('.key[data-key="' + ev[5] + '"]') : null;
      if (cell) {
        cell.classList.add("pressing");
        current = cell;
      }
      // 实时按键条：新键从右侧追加；复制 / 粘贴显示内容，图片显示缩略图
      ltPush(isClip ? (ev[6] + "：" + ev[7]) : ev[6],
        ev[0] + " " + ev[2] + "　前台：" + ev[3], isClip, ev[8], ev[9] || "", wait);
      if (pbInfo) {
        pbInfo.textContent = "回放中 " + (i + 1) + " / " + list.length
          + (isClip ? "　最近动作：" + ev[6] : "　最近按下：" + ev[6]);
      }
      timer = window.setTimeout(function () { step(list, i + 1, ev); }, wait);
    }
    function beginFromStart() {
      var list = pick();
      if (!list.length) {
        if (pbInfo) pbInfo.textContent = "所选日期 / 时间 / 程序下没有可回放的事件";
        return;
      }
      stop(false);       // 重新开始：清空走马灯，从第一条重新放
      playing = true;
      playBtn.textContent = "\u23F8 暂停";
      step(list, 0, null);
    }
    playBtn.addEventListener("click", function () {
      if (playing) {
        // 暂停：保留下面已经显示出来的按键，方便停下来慢慢看
        stop(true);
        if (pbInfo) pbInfo.textContent = "已暂停（点「回放」继续，或点「重新开始」从头再来）";
        return;
      }
      beginFromStart();
    });
    if (restartBtn) {
      restartBtn.addEventListener("click", function () { beginFromStart(); });
    }
    if (daySel) daySel.addEventListener("change", function () { stop(true); refreshCount(); });
    if (procSel) procSel.addEventListener("change", function () { stop(true); refreshCount(); });
    if (fromInp) fromInp.addEventListener("input", function () { stop(true); refreshCount(); });
    if (toInp) toInp.addEventListener("input", function () { stop(true); refreshCount(); });
    refreshCount();
  }
})();
</script>
</body></html>"""


def _lerp(c1, c2, t: float) -> str:
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(
        int(round(a + (b - a) * t)) for a, b in zip(c1, c2)
    )


def _heat(value: int, maximum: int) -> str:
    if value <= 0:
        return "#273040"
    ratio = (value / maximum) ** 0.55 if maximum > 0 else 0.0
    if ratio < 0.5:
        return _lerp(_HEAT_LOW, _HEAT_MID, ratio / 0.5)
    return _lerp(_HEAT_MID, _HEAT_HIGH, (ratio - 0.5) / 0.5)


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 24:
        # 超过一天：用"天 + 小时"表示，如 30 小时 -> 1 天 6 小时
        return "%d 天 %d 小时" % (h // 24, h % 24)
    if h:
        return "%d 小时 %d 分" % (h, m)
    if m:
        return "%d 分 %d 秒" % (m, s)
    return "%d 秒" % s


# 亮暗切换脚本：跟着报告一起内嵌，点右上角按钮在两套配色之间切，
# 选择存 localStorage（file:// 下也可用），下次打开还保持上次的选择。
_THEME_SCRIPT = """<script>
(function () {
  var root = document.documentElement;
  var btn = document.getElementById("themeBtn");
  if (!btn) return;
  function label(mode) { return mode === "light" ? "暗色" : "明亮"; }
  function apply(mode) {
    root.setAttribute("data-theme", mode);
    var t = document.getElementById("themeBtnText");
    if (t) t.textContent = label(mode);
    try { localStorage.setItem("keylens-theme", mode); } catch (e) {}
  }
  var cur = root.getAttribute("data-theme");
  if (cur !== "light" && cur !== "dark") {
    try { cur = localStorage.getItem("keylens-theme"); } catch (e) {}
    if (cur !== "light" && cur !== "dark") cur = "dark";
    root.setAttribute("data-theme", cur);
  }
  var t = document.getElementById("themeBtnText");
  if (t) t.textContent = label(cur);
  btn.addEventListener("click", function () {
    apply(root.getAttribute("data-theme") === "light" ? "dark" : "light");
  });
})();
</script>"""

_LOGO_CACHE: Optional[str] = None


def _logo_img() -> str:
    """顶栏 logo 的 <img> 标签：把 app.ico 转成 32px PNG 以 data URI 内嵌。

    报告是单文件 HTML，不能引用外部图片；app.ico 有 256px 的一帧、原始 96KB，
    直接内嵌每份报告都会大 100 多 KB，所以有 Pillow（可选依赖）时缩到 32px 再内嵌。
    找不到图标文件时返回空串，顶栏退回 CSS 渐变方块。
    """
    global _LOGO_CACHE
    if _LOGO_CACHE is not None:
        return _LOGO_CACHE
    import base64
    import io
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(here), "tools", "app.ico"),
        os.path.join(here, "app.ico"),
        os.path.join(getattr(sys, "_MEIPASS", "") or "", "app.ico"),
        os.path.join(os.path.dirname(getattr(sys, "executable", "")) or "", "app.ico"),
    ]
    data = b""
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                break
            except OSError:
                continue
    if not data:
        _LOGO_CACHE = ""
        return ""
    try:
        from PIL import Image  # 可选依赖，缺失时退回原始 ico 字节
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGBA").resize((32, 32), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
        mime, payload = "image/png", buf.getvalue()
    except Exception:
        mime, payload = "image/x-icon", data
    _LOGO_CACHE = '<img src="data:%s;base64,%s" alt="KeyLens" width="22" height="22">' % (
        mime, base64.b64encode(payload).decode("ascii"))
    return _LOGO_CACHE


def _content_name(name: str) -> str:
    """剪贴板内容类型 -> 中文显示名。"""
    if clipboard_module is not None:
        return clipboard_module.CONTENT_DISPLAY.get(name, name)
    return name


def _hour_label(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _row_units(row) -> float:
    """一行布局占多少个基准键位宽度（用于各列之间的 flex 比例）。"""
    total = 0.0
    for item in row:
        if item is None:
            total += 1.0
        elif isinstance(item, tuple):
            total += float(item[1])
        else:
            total += float(item)
    return total


def _render_heatmap(counts: Dict[str, int]) -> str:
    """全键盘布局：主键盘区在左，控制键区 / 数字键区作为右侧两列。"""
    maximum = max([counts.get(k, 0) for k in keymap.heatmap_keys()] + [1])

    def key_html(key: str, value: int, style: str) -> str:
        label = html.escape(keymap.display(key))
        return (
            '<div class="key" data-key="%s" style="%sbackground:%s" title="%s：%d 次">'
            "<span>%s</span><span class='n'>%s</span></div>"
            % (html.escape(key, quote=True), style, _heat(value, maximum),
               label, value, label, value if value else "")
        )

    def flex_row(row) -> str:
        parts = ['<div class="row">']
        for item in row:
            if item is None:
                # 被跨行键占掉的空位，占一格宽度保持对齐
                parts.append('<div class="gap" style="flex:1"></div>')
                continue
            if not isinstance(item, tuple):
                # 指定宽度的空隙
                parts.append('<div class="gap" style="flex:%.2f"></div>' % float(item))
                continue
            key, width = item[0], float(item[1])
            parts.append(key_html(key, counts.get(key, 0), "flex:%.2f;" % width))
        parts.append("</div>")
        return "".join(parts)

    def numpad_grid(rows) -> str:
        # 数字键区含纵向跨行的 + / Enter（三元组带高度），用 CSS Grid 摆放
        occupied = set()
        parts = ['<div class="kbgrid">']
        for r, row in enumerate(rows, 1):
            c = 0
            for item in row:
                c += 1
                while (r, c) in occupied:
                    c += 1
                if item is None:
                    continue
                if len(item) == 3:
                    key, w, h = item[0], int(item[1]), int(item[2])
                else:
                    key, w, h = item[0], int(item[1]), 1
                for rr in range(r, r + h):
                    for cc in range(c, c + w):
                        occupied.add((rr, cc))
                style = "grid-column:%d / span %d;grid-row:%d / span %d;" % (c, w, r, h)
                parts.append(key_html(key, counts.get(key, 0), style))
        parts.append("</div>")
        return "".join(parts)

    left_rows = [keymap.FUNCTION_ROW] + list(keymap.KEYBOARD_ROWS)  # type: ignore[list-item]
    parts = ['<div class="kblayout">']
    # 各列宽度按"最多键位单位的一行"成比例分配，保证三列的单位键宽基本一致
    parts.append('<div class="kbcol" style="flex:%.2f 1 0">' % max(_row_units(r) for r in left_rows))
    for row in left_rows:
        parts.append(flex_row(row))
    parts.append("</div>")
    parts.append('<div class="kbcol" style="flex:%.2f 1 0">' % max(_row_units(r) for r in keymap.NAV_ROWS))
    for row in keymap.NAV_ROWS:
        if all(item is None for item in row):
            # 空行：渲染成固定高度的间隔，保持方向键与两侧列的底边对齐
            parts.append('<div class="row" style="height:46px"></div>')
        else:
            parts.append(flex_row(row))
    parts.append("</div>")
    parts.append('<div class="kbcol" style="flex:%.2f 1 0">' % max(_row_units(r) for r in keymap.NUMPAD_ROWS))
    parts.append(numpad_grid(keymap.NUMPAD_ROWS))
    parts.append("</div></div>")
    return "".join(parts)


def _render_hours(hourly: List[int]) -> str:
    maximum = max(hourly + [1])
    peak = max(range(24), key=lambda h: hourly[h] if h < len(hourly) else 0)
    parts = ['<div class="hours">']
    for hour in range(24):
        value = hourly[hour] if hour < len(hourly) else 0
        height = (value / maximum) * 100.0
        # 每 3 小时一个刻度标签，其余留空，避免 24 个标签挤在一起
        label = "%02d" % hour if hour % 3 == 0 else "&nbsp;"
        cls = "bar peak" if value and hour == peak else "bar"
        parts.append(
            '<div class="hour" title="%02d:00 — %d 次">'
            '<div class="%s" style="height:%.1f%%"></div><span>%s</span></div>'
            % (hour, value, cls, max(1.0, height) if value else 0.0, label)
        )
    parts.append("</div>")
    return "".join(parts)


def _render_legend() -> str:
    """热力图图例：按键少 → 多 的渐变色带。"""
    idle = "#273040"
    low = _lerp(_HEAT_LOW, _HEAT_MID, 0.0)
    mid = _lerp(_HEAT_LOW, _HEAT_MID, 1.0)
    high = _lerp(_HEAT_MID, _HEAT_HIGH, 1.0)
    return (
        '<div class="legend"><span>按键少</span>'
        '<i class="lg" style="background:linear-gradient(90deg,%s,%s,%s,%s)"></i>'
        '<span>按键多</span></div>'
        % (idle, low, mid, high)
    )


def _render_table(headers: List[str], rows: List[List[object]]) -> str:
    """渲染表格。单元格默认转义；用 :class:`Raw` 包裹的内容按原始 HTML 插入。"""
    out = ['<div class="tbl"><table><thead><tr>']
    out.extend("<th>%s</th>" % html.escape(h) for h in headers)
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for cell in row:
            if isinstance(cell, Raw):
                out.append("<td>%s</td>" % cell.html)
            else:
                out.append("<td>%s</td>" % html.escape(str(cell)))
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


_TIMELINE_COLS = ["160px", "80px", "220px", "auto"]


def _count_procs(entries) -> List[Tuple[str, int]]:
    """从 (单元格, 日期, 秒数, 程序) 四元组列表里统计程序出现次数，按次数降序。"""
    counts: Dict[str, int] = {}
    for entry in entries:
        proc = entry[3]
        counts[proc] = counts.get(proc, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _day_options(entries, start_day: str, end_day: str) -> List[Tuple[str, int]]:
    """日期下拉选项：固定列出 start_day..end_day 的每一天及当日记录条数（升序）。"""
    counts: Dict[str, int] = {}
    for entry in entries:
        counts[entry[1]] = counts.get(entry[1], 0) + 1
    days: List[Tuple[str, int]] = []
    cur = datetime.strptime(start_day, "%Y-%m-%d")
    stop = datetime.strptime(end_day, "%Y-%m-%d")
    while cur <= stop:
        ds = cur.strftime("%Y-%m-%d")
        days.append((ds, counts.get(ds, 0)))
        cur += timedelta(days=1)
    return days


def _render_filterbar(
    target_id: str,
    procs: List[Tuple[str, int]],
    days: Optional[List[Tuple[str, int]]] = None,
    default_day: str = "",
) -> str:
    """明细表筛选条：日期下拉 + 程序下拉 + 起止时间，行为由页面尾部的脚本驱动。

    ``days`` 是 (日期, 当日条数) 列表，选项显示成 "2026-10-22 周四（312 条）"。
    """
    options = ['<option value="">全部程序</option>']
    for name, _count in procs:
        options.append('<option value="%s">%s</option>'
                       % (html.escape(name, quote=True), html.escape(name)))
    day_options = []
    for d, count in (days or []):
        if count <= 0:
            continue  # 没有记录的日期不显示，避免下拉里出现一堆无意义的 0 条选项
        try:
            weekday = _WEEKDAYS[datetime.strptime(d, "%Y-%m-%d").weekday()]
            label = "%s %s（%s 条）" % (d, weekday, "{:,}".format(count))
        except ValueError:
            label = d
        sel = " selected" if d == default_day else ""
        day_options.append('<option value="%s"%s>%s</option>'
                           % (html.escape(d), sel, html.escape(label)))
    return (
        '<div class="filterbar" data-target="%s">'
        '<label>日期 <select class="f-day">%s</select></label>'
        '<label>程序 <select class="f-proc">%s</select></label>'
        '<label>从 <input type="time" class="f-from" step="1"></label>'
        '<label>至 <input type="time" class="f-to" step="1"></label>'
        '<button type="button" class="f-clear">重置</button>'
        '<span class="f-count"></span></div>'
        % (target_id, "".join(day_options), "".join(options))
    )


def _row_attrs(day: str, sec: int, proc: str, key: str = "") -> str:
    """明细行的筛选属性：年月日 + 当天秒数 + 前台程序（+ 键位，供回放动画定位）。"""
    attrs = 'data-day="%s" data-sec="%d" data-proc="%s"' % (
        html.escape(day), sec, html.escape(proc, quote=True),
    )
    if key:
        attrs += ' data-key="%s"' % html.escape(key, quote=True)
    return attrs


def _clip_preview(content: str, note: str, text_len: int) -> str:
    """剪贴板内容预览：只有开启"保存正文"时才有 note，否则只给类型与规模。

    图片的 note 是临时 .bmp 路径，不该出现在报告里，这里只写「（图片）」。
    """
    if content == "图片":
        return "（图片）"
    if note:
        flat = " ".join(note.split())
        return flat[:80] + ("…" if len(flat) > 80 else "")
    if text_len:
        return "（%s，共 %s 字符，未保存正文）" % (content, "{:,}".format(text_len))
    return "（%s，未保存正文）" % content


# 图片类复制在没有可用文件（或文件太大、格式浏览器解不了）时的占位标记：
# 前端会画一个纯 CSS 的缩略图块，不依赖任何图片解码
_IMG_PLACEHOLDER = "placeholder"
# 单张图片最多内嵌多少像素（约 3000×1000），超过就不塞进报告
_MAX_IMAGE_PIXELS = 3_000_000


def _bmp_to_png_bytes(data: bytes) -> bytes:
    """把 BMP / DIB 像素数据转成 PNG 字节（纯标准库）。

    剪贴板里的图多是 DIB 转存的 .bmp：可能是 32bpp、BI_BITFIELDS、V4/V5 头，
    浏览器对这类 BMP 支持很差（Chrome 基本解不出），所以统一转成 PNG 再内嵌。
    """
    if len(data) < 26 or data[:2] != b"BM":
        raise ValueError("不是 BMP 文件")
    pixel_off = struct.unpack_from("<I", data, 10)[0]
    dib = data[14:]
    bi_size = struct.unpack_from("<I", dib, 0)[0]
    if bi_size > len(dib):
        bi_size = len(dib)  # 声明的头比文件还长（截断/不规范）时按实际长度兜底
    if bi_size == 12:  # BITMAPCOREHEADER
        width, height = struct.unpack_from("<hh", dib, 4)
        _planes, bitcount = struct.unpack_from("<HH", dib, 8)
        compression = 0
    elif bi_size >= 40:
        width, height, _planes, bitcount, compression = struct.unpack_from("<iiHHI", dib, 4)
    else:
        raise ValueError("DIB 头异常")
    if compression not in (0, 3):
        raise ValueError("暂不支持的压缩方式")
    header_end = 14 + bi_size + (12 if (compression == 3 and bi_size == 40) else 0)
    top_down = height < 0
    height = abs(height)
    if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
        raise ValueError("图片过大")
    if bitcount not in (1, 4, 8, 16, 24, 32):
        raise ValueError("不支持的位深")
    stride = ((width * bitcount + 31) // 32) * 4
    if not (header_end <= pixel_off <= len(data)) or pixel_off + stride * height > len(data):
        pixel_off = header_end  # 文件头里的偏移不可信时按 DIB 结构重算
    if pixel_off + stride * height > len(data):
        raise ValueError("像素数据不完整")

    palette: List[bytes] = []
    if bitcount <= 8:
        for k in range(header_end, min(pixel_off, len(data)) - 3, 4):
            palette.append(bytes((data[k + 2], data[k + 1], data[k])))

    raw = bytearray()
    for y in range(height):
        src = pixel_off + (y if top_down else height - 1 - y) * stride
        line = data[src:src + stride]
        raw += b"\x00"  # 每行的 filter 类型 0
        if bitcount == 32:
            out = bytearray(width * 3)
            out[0::3] = line[2::4]  # R
            out[1::3] = line[1::4]  # G
            out[2::3] = line[0::4]  # B
            raw += out
        elif bitcount == 24:
            out = bytearray(width * 3)
            out[0::3] = line[2::3]
            out[1::3] = line[1::3]
            out[2::3] = line[0::3]
            raw += out
        elif bitcount == 16:
            out = bytearray(width * 3)
            for i in range(width):
                v = line[i * 2] | (line[i * 2 + 1] << 8)
                out[i * 3] = ((v >> 10) & 31) << 3
                out[i * 3 + 1] = ((v >> 5) & 31) << 2
                out[i * 3 + 2] = (v & 31) << 3
            raw += out
        else:  # 1 / 4 / 8 位：走调色板
            per = 8 // bitcount
            mask = (1 << bitcount) - 1
            for i in range(width):
                byte = line[i // per]
                shift = 8 - bitcount * (i % per + 1)
                idx = (byte >> shift) & mask
                raw += palette[idx] if idx < len(palette) else b"\x00\x00\x00"

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def _clip_media(content: str, note: str) -> str:
    """剪贴板里的图片 -> 可内嵌的 data URI（统一转PNG），失败时返回占位标记。

    程序把复制的图片存成临时 .bmp（路径写在 note 里，保留 3 天）；报告是单文件，
    所以这里把图片转成 PNG 后以 base64 内嵌，浏览器一定能显示。
    """
    if content != "图片":
        return ""
    if not note:
        return _IMG_PLACEHOLDER
    try:
        if not os.path.isfile(note):
            return _IMG_PLACEHOLDER
        if os.path.getsize(note) > 12 * 1024 * 1024:
            return _IMG_PLACEHOLDER
        with open(note, "rb") as fh:
            data = fh.read()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        png = _bmp_to_png_bytes(data)
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    except Exception:
        return _IMG_PLACEHOLDER


def _clip_thumb_html(media: str, path: str = "") -> str:
    """明细表里的缩略图单元格：能内嵌就出真图（可点开），否则出纯 CSS 占位块。"""
    if media.startswith("data:"):
        title = ' title="%s"' % html.escape(path, quote=True) if path else ""
        return '<img src="%s" alt="剪贴板图片"%s>' % (media, title)
    return '<span class="thumbph">图片</span>'


def _render_playbar(events: List[Tuple[str, int, str, str, str, str, str, str, str, str]]) -> str:
    """模拟键盘回放控制条：播放/暂停 + 日期 / 程序 / 节奏选择 + 内嵌事件序列。

    ``events`` 每项是 (日期, 当天秒数, 时:分:秒, 程序, 类型, 键位, 显示名, 文本, 图片dataURI, 图片路径)，
    序列化成 JSON 内嵌页面，回放完全在这把全键盘上进行，不依赖下方任何表格。
    """
    day_counts: Dict[str, int] = {}
    proc_counts: Dict[str, int] = {}
    for day, _sec, _when, proc, _kind, _key, _label, _text, _media, _path in events:
        day_counts[day] = day_counts.get(day, 0) + 1
        proc_counts[proc] = proc_counts.get(proc, 0) + 1
    day_options = ['<option value="">全部日期</option>']
    for d, n in sorted(day_counts.items(), reverse=True):
        day_options.append('<option value="%s">%s（%s 次）</option>'
                           % (html.escape(d), html.escape(d), "{:,}".format(n)))
    proc_options = ['<option value="">全部程序</option>']
    for name, n in sorted(proc_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        proc_options.append('<option value="%s">%s（%s 次）</option>'
                            % (html.escape(name, quote=True), html.escape(name), "{:,}".format(n)))
    payload = json.dumps(
        [[d, sec, when, proc, kind, key, label, text, media, path]
         for d, sec, when, proc, kind, key, label, text, media, path in events],
        ensure_ascii=False, separators=(",", ":"),
    ).replace("%", "%%")  # 页面模板用 % 格式化，字面百分号要转义
    return (
        '<div class="playbar">'
        '<button type="button" class="pb-play">\u25b6 回放</button>'
        '<button type="button" class="pb-restart">\u21ba 重新开始</button>'
        '<label>日期 <select class="pb-day">%s</select></label>'
        '<label>时间从 <input type="time" class="pb-from" step="1"></label>'
        '<label>到 <input type="time" class="pb-to" step="1"></label>'
        '<label>程序 <select class="pb-proc">%s</select></label>'
        '<label>倍速 <select class="pb-speed">'
        '<option value="0.5">0.5 倍</option>'
        '<option value="1">1 倍（原速）</option>'
        '<option value="1.5">1.5 倍</option>'
        '<option value="2" selected>2 倍</option>'
        '<option value="3">3 倍</option>'
        '<option value="4">4 倍</option>'
        '</select></label>'
        '<span class="pb-info">共 %s 次事件可回放；按年月日 + 时间段 + 程序筛选后，在下面这把键盘上按时间顺序逐个点亮。</span>'
        '</div>'
        '<script type="application/json" id="kb-events">%s</script>'
        % ("".join(day_options), "".join(proc_options),
           "{:,}".format(len(events)), payload)
    )


def _render_livetip() -> str:
    """键盘下方的「实时按键」显示条：按下的键从右侧进场，排满宽度后向左滑出。"""
    return (
        '<div class="livetip" id="kb-live">'
        '<span class="lt-label">实时按键</span>'
        '<div class="lt-main" id="kb-live-main">'
        '<span class="lt-idle" id="kb-live-idle">点上方「▶ 回放」开始</span>'
        '<span class="lt-track" id="kb-live-track"></span>'
        '</div>'
        '<div class="lt-sub" id="kb-live-sub">按下的键从右侧排入，排满后从左侧滑出；'
        '复制 / 粘贴显示内容，悬停看时间，可框选复制；'
        '图片可点击放大、右键复制或复制路径。</div>'
        '</div>'
        '<div class="kb-lightbox" id="kb-lightbox">'
        '<img id="kb-lightbox-img" alt="剪贴板图片">'
        '<div class="lb-cap" id="kb-lightbox-cap"></div>'
        '</div>'
    )


def _render_timeline_table(
    headers: List[str],
    rows: List[Tuple[List[object], str]],
    tbody_id: str = "",
    col_widths: Optional[List[str]] = None,
    center_cols: Optional[List[int]] = None,
) -> str:
    """渲染带固定表头、可滚动 tbody 的明细表格。

    rows 的每项是 (单元格列表, 行属性字符串)；行属性用于页面内筛选。
    ``center_cols`` 里的列号（1 起）内容水平居中。
    """
    cols = col_widths or _TIMELINE_COLS
    centered = set(center_cols or [])

    def tag(name: str, idx: int, content: str) -> str:
        cls = ' class="tc"' if idx in centered else ""
        return "<%s%s>%s</%s>" % (name, cls, content, name)

    out = ['<div class="timeline-wrap"><table class="timeline-table"><colgroup>']
    out.extend('<col style="width:%s">' % w for w in cols)
    out.append("</colgroup><thead><tr>")
    out.extend(tag("th", i + 1, html.escape(h)) for i, h in enumerate(headers))
    out.append("</tr></thead></table>")
    out.append('<div class="timeline-body"><table class="timeline-table"><colgroup>')
    out.extend('<col style="width:%s">' % w for w in cols)
    out.append('</colgroup><tbody%s>' % (' id="%s"' % tbody_id if tbody_id else ""))
    for cells, attrs in rows:
        out.append("<tr %s>" % attrs if attrs else "<tr>")
        for i, cell in enumerate(cells):
            content = cell.html if isinstance(cell, Raw) else html.escape(str(cell))
            out.append(tag("td", i + 1, content))
        out.append("</tr>")
    out.append("</tbody></table></div></div>")
    return "".join(out)


class Raw:
    """标记"这段是可信 HTML，不要再转义"。"""

    __slots__ = ("html",)

    def __init__(self, html_text: str):
        self.html = html_text


def build_html(db: Database, day: str, config: Config, title_suffix: str = "") -> str:
    """渲染一天的完整报告页。"""
    summary: DaySummary = db.day_summary(day)
    return _render_page(db, summary, day, title_suffix)


def _render_page(db: Database, summary: DaySummary, day: str, title_suffix: str = "") -> str:
    """把统计数据填进 :data:`_PAGE_TEMPLATE`。

    单独拆出来是为了能被 :mod:`keylens.selftest` 直接调用做格式化契约校验：
    占位符与字典键不匹配时这里就会抛 ``TypeError``，绝不能等到用户点"生成报告"才发现。
    """

    # 远程 / 本地对照
    remote_keys = 0
    local_keys = 0
    for session in summary.sessions:
        if session.remote:
            remote_keys += session.key_count
        else:
            local_keys += session.key_count

    def _bar(pct: float, label: str) -> Raw:
        """占比单元格：迷你进度条 + 百分比文本。"""
        return Raw('<span class="bar"><span style="width:%.0f%%"></span></span>%s'
                   % (min(100.0, max(0.0, pct)), label))

    categories = [
        [keymap.CATEGORY_DISPLAY.get(name, name), "{:,}".format(count),
         _bar(count * 100.0 / summary.key_count if summary.key_count else 0.0,
              "%.1f%%" % (count * 100.0 / summary.key_count) if summary.key_count else "0%")]
        for name, count in sorted(summary.by_category.items(), key=lambda kv: kv[1], reverse=True)
    ]

    top_keys = [
        [str(rank), keymap.display(key), "{:,}".format(count),
         keymap.CATEGORY_DISPLAY.get(keymap.category(key), "")]
        for rank, (key, count) in enumerate(
            sorted(summary.by_key.items(), key=lambda kv: kv[1], reverse=True)[:25], 1)
        if key != "MASKED"
    ]

    top_procs = [
        [name or "(未知)", "{:,}".format(count),
         _bar(count * 100.0 / summary.key_count if summary.key_count else 0.0,
              "%.1f%%" % (count * 100.0 / summary.key_count) if summary.key_count else "0%")]
        for name, count in sorted(summary.by_process.items(), key=lambda kv: kv[1], reverse=True)
    ]

    # 远程控制 / 监控软件各自的输入量（从按键的进程分布里筛出远控软件进程）
    remote_app_rows: List[List[object]] = []
    if summary.by_process:
        seen = set()
        for name, count in summary.by_process.items():
            stem = (name or "").rsplit(".", 1)[0].lower()
            if stem in winapi.REMOTE_PROCESS_NAMES and name not in seen:
                seen.add(name)
                label = winapi.REMOTE_APP_LABELS.get(stem, stem)
                remote_app_rows.append([label, name, "{:,}".format(count)])
        remote_app_rows.sort(key=lambda r: int(r[2].replace(",", "")), reverse=True)
    remote_apps_html = (
        _render_table(["软件", "进程", "按键数"], remote_app_rows)
        if remote_app_rows
        else '<p class="empty">当天没有在远程控制 / 监控软件中的输入记录。</p>'
    )

    session_rows = []
    for session in summary.sessions:
        cls = "remote" if session.remote else "local"
        tag = "远程会话" if session.remote else "本地输入"
        note = session.label or session.remote_note or ""
        session_rows.append([
            "#%d" % session.id,
            _hour_label(session.started_at),
            _hour_label(session.ended_at) if session.ended_at else "（进行中）",
            _fmt_duration(session.duration),
            "{:,}".format(session.key_count),
            Raw('<span class="badge %s">%s</span>' % (cls, tag)),
            note[:80],
        ])

    active = summary.active_minutes
    kpm = (summary.key_count / active) if active > 0 else 0.0

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_dir = os.path.abspath(os.path.dirname(db.path))

    session_table = (
        _render_table(["会话", "开始", "结束", "时长", "按键数", "类型", "备注"], session_rows)
        if session_rows
        else '<p class="empty">暂无数据</p>'
    )

    clip = db.clipboard_summary(day)
    clip_overview = [
        ["复制（剪贴板内容变化）", "{:,}".format(clip.copy_count),
         "其中掩码 %d 次" % clip.masked_count],
        ["粘贴（Ctrl+V / Shift+Insert）", "{:,}".format(clip.paste_count),
         "其中远程注入 %d 次" % clip.remote_count],
        ["涉及文本字符合计", "{:,}".format(clip.text_chars), "仅统计非掩码的文本类内容"],
    ]
    clip_parts = [_render_table(["项目", "次数", "备注"], clip_overview)]
    if clip.by_content:
        clip_parts.append('<div class="blocktitle">内容类型分布</div>')
        clip_parts.append(
            _render_table(
                ["类型", "次数"],
                [[_content_name(name), "{:,}".format(count)]
                 for name, count in sorted(clip.by_content.items(), key=lambda kv: kv[1], reverse=True)],
            )
        )
    if clip.by_process:
        clip_parts.append('<div class="blocktitle">来源应用分布</div>')
        clip_parts.append(
            _render_table(
                ["前台应用", "次数"],
                [[name, "{:,}".format(count)]
                 for name, count in sorted(clip.by_process.items(), key=lambda kv: kv[1], reverse=True)][:12],
            )
        )

    # 剪贴板明细：逐条列出最近 7 天的复制 / 粘贴动作（不含正文内容），
    # 支持按日期、时间与程序筛选。掩码记录不显示来源程序与窗口标题，与总览的隐私口径一致。
    clip_entries: List[Tuple[List[object], str, int, str, str, str]] = []
    masked_skipped = 0  # 命中隐私掩码的记录：正文与来源都没记，报告里不展示
    start_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    try:
        conn = db.connect()
        try:
            for r in conn.execute(
                "SELECT * FROM (SELECT ts, kind, content, text_len, injected, process, title, masked,"
                " note FROM clipboard WHERE day >= ? ORDER BY ts DESC LIMIT 2000) ORDER BY ts ASC",
                (start_day,),
            ):
                if int(r["masked"] or 0):
                    masked_skipped += 1
                    continue  # 掩码记录不展示，避免走马灯里刷出一串"（掩码）"
                ts = float(r["ts"]) if r["ts"] is not None else None
                if ts:
                    dt = datetime.fromtimestamp(ts)
                    when = dt.strftime("%Y-%m-%d %H:%M:%S")
                    row_day = dt.strftime("%Y-%m-%d")
                    sec = dt.hour * 3600 + dt.minute * 60 + dt.second
                else:
                    when, row_day, sec = "-", day, 0
                kind = "粘贴" if r["kind"] == "paste" else "复制"
                proc = r["process"] or "(未知)"
                title = (r["title"] or "")[:80]
                content = _content_name(r["content"])
                note = r["note"] or ""
                preview = _clip_preview(content, note, int(r["text_len"] or 0))
                media = _clip_media(content, note)
                # 图片的 note 就是临时文件路径，报告里给用户一个可复制的来源
                path = note if content == "图片" else ""
                inj = int(r["injected"] or 0)
                clip_entries.append((
                    [
                        when, kind, content,
                        "{:,}".format(int(r["text_len"] or 0)),
                        preview,
                        Raw('<span class="badge %s">%s</span>'
                            % ("remote" if inj else "local", "远程" if inj else "本地")),
                        proc, title,
                    ],
                    row_day, sec, proc, media, path,
                ))
        finally:
            conn.close()
    except Exception:
        clip_entries = []
    if masked_skipped:
        clip_parts.append('<p class="blocktitle">另有 %d 条命中隐私掩码的剪贴板记录，'
                          '按隐私规则不在报告里展示。</p>' % masked_skipped)
    if clip_entries:
        if len(clip_entries) >= 2000:
            clip_parts.append('<p class="blocktitle">最近 7 天剪切板明细较多，仅显示最近 2000 条。</p>')
        clip_parts.append('<div class="blocktitle">剪切板明细（最近 7 天，可按日期 / 时间 / 程序筛选）</div>')
        clip_parts.append(_render_filterbar(
            "clip-rows", _count_procs(clip_entries),
            days=_day_options(clip_entries, start_day, day), default_day=day,
        ))
        clip_parts.append(_render_timeline_table(
            ["时间", "动作", "类型", "字符数", "内容预览", "来源", "前台应用", "窗口标题"],
            [(cells[:-1] + [Raw('<span class="thumbcell">%s</span>' % _clip_thumb_html(media, path))],
              _row_attrs(row_day, sec, proc))
             for cells, row_day, sec, proc, media, path in clip_entries],
            tbody_id="clip-rows",
            col_widths=["160px", "60px", "80px", "70px", "240px", "90px", "220px", "auto"],
            center_cols=[2, 4, 6],
        ))
    elif not clip.total:
        clip_parts.append('<p class="empty">当天没有可展示的剪贴板明细。</p>')
    clipboard_table = "".join(clip_parts) if clip.total or clip_entries else (
        '<p class="empty">当天没有记录到复制 / 粘贴（可能未开启剪贴板监听，或这段时间没有复制粘贴动作）。</p>'
    )

    # 按键时间线：按时间顺序列出最近 7 天的"非掩码"按键，便于回看输入轨迹，
    # 支持按日期、时间与程序筛选。
    # 掩码键（密码框、掩码进程里的按键）key_id 为 'MASKED'，这里直接排除，
    # 既满足"按顺序看输入"的需求，又绝不会把口令序列落进报告。
    timeline_entries: List[Tuple[List[object], str, int, str, str]] = []
    timeline_total = 0
    try:
        conn = db.connect()
        try:
            timeline_total = int(conn.execute(
                "SELECT COUNT(*) FROM events WHERE day >= ? AND key_id != 'MASKED'",
                (start_day,),
            ).fetchone()[0] or 0)
            for r in conn.execute(
                "SELECT * FROM (SELECT ts, key_id, process, title FROM events "
                "WHERE day >= ? AND key_id != 'MASKED' ORDER BY ts DESC LIMIT 3000) ORDER BY ts ASC",
                (start_day,),
            ):
                ts = float(r["ts"]) if r["ts"] is not None else None
                if ts:
                    dt = datetime.fromtimestamp(ts)
                    when = dt.strftime("%Y-%m-%d %H:%M:%S")
                    row_day = dt.strftime("%Y-%m-%d")
                    sec = dt.hour * 3600 + dt.minute * 60 + dt.second
                else:
                    when, row_day, sec = "-", day, 0
                proc = r["process"] or "(未知)"
                timeline_entries.append((
                    [when, keymap.display(r["key_id"]), proc, (r["title"] or "")[:120]],
                    row_day, sec, proc, r["key_id"],
                ))
        finally:
            conn.close()
    except Exception:
        timeline_entries = []

    if not timeline_entries:
        timeline_html = '<p class="empty">最近 7 天没有非掩码的按键记录。</p>'
    else:
        timeline_html = ""
        if timeline_total > len(timeline_entries):
            timeline_html = (
                '<p class="blocktitle">最近 7 天共 %s 条非掩码按键，为避免报告过大仅显示最近 %s 条。</p>'
                % ("{:,}".format(timeline_total), "{:,}".format(len(timeline_entries)))
            )
        timeline_html += _render_filterbar(
            "tl-rows", _count_procs(timeline_entries),
            days=_day_options(timeline_entries, start_day, day), default_day=day,
        )
        timeline_html += _render_timeline_table(
            ["时间", "键位", "前台应用", "窗口标题"],
            [(cells, _row_attrs(row_day, sec, proc))
             for cells, row_day, sec, proc, _key_id in timeline_entries],
            tbody_id="tl-rows",
        )

    # 回放事件流：按键 + 复制/粘贴按时间合并，回放时既点亮键位、也在"实时按键"条里显示内容。
    play_events: List[Tuple[str, int, str, str, str, str, str, str, str, str]] = []
    for cells, row_day, sec, proc, key_id in timeline_entries:
        play_events.append((row_day, sec, str(cells[0]), proc, "key", key_id,
                            keymap.display(key_id), "", "", ""))
    for cells, row_day, sec, proc, media, path in clip_entries:
        label = str(cells[1])  # 复制 / 粘贴
        play_events.append((row_day, sec, str(cells[0]), proc,
                            "copy" if label == "复制" else "paste", "",
                            label, str(cells[4]), media, path))
    play_events.sort(key=lambda e: (e[0], e[1]))

    values = {
        "day": html.escape(day),
        "suffix": html.escape(title_suffix),
        "style": _STYLE,
        "logo": _logo_img(),
        "theme_js": _THEME_SCRIPT,
        "generated": generated,
        "db": html.escape(os.path.basename(db.path)),
        "total": "{:,}".format(summary.key_count),
        "kpm": kpm,
        "active": _fmt_duration(active * 60.0) if active else "-",
        "sessions": session_table,
        "session_count": len(summary.sessions),
        "masked": "{:,}".format(summary.masked_count),
        "coverage": summary.coverage * 100.0,
        "remote": "{:,}".format(remote_keys),
        "local": "{:,}".format(local_keys),
        "clip_copy": "{:,}".format(clip.copy_count),
        "clip_paste": "{:,}".format(clip.paste_count),
        "clipboard": clipboard_table,
        "heatmap": _render_heatmap(summary.by_key),
        "playback": _render_playbar(play_events),
        "livetip": _render_livetip(),
        "legend": _render_legend(),
        "hours": _render_hours(summary.hourly),
        "top_keys": _render_table(["排名", "键位", "次数", "类别"], top_keys) if top_keys else '<p class="empty">暂无数据</p>',
        "categories": _render_table(["类别", "次数", "占比"], categories) if categories else '<p class="empty">暂无数据</p>',
        "top_procs": _render_table(["前台应用", "按键数", "占比"], top_procs) if top_procs else '<p class="empty">暂无数据</p>',
        "remote_apps": remote_apps_html,
        "timeline": timeline_html,
        "data_dir": html.escape(data_dir),
    }
    page = _PAGE_TEMPLATE % values
    return page


def write_daily_report(db: Database, config: Config, day: Optional[str] = None) -> str:
    """写出报告文件，返回文件路径。"""
    from .core.db import day_str

    target = day or day_str()
    content = build_html(db, target, config)
    out_dir = os.path.join(config.data_dir, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "keylens-%s.html" % target)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def write_range_report(db: Database, config: Config, days: int = 7) -> str:
    """导出"最近 N 天"的汇总报告（只做汇总，不逐日列明细）。"""
    rows = []
    for offset in range(days - 1, -1, -1):
        day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        summary = db.day_summary(day)
        rows.append([
            day,
            "{:,}".format(summary.key_count),
            _fmt_duration(summary.active_minutes * 60.0) if summary.active_minutes else "-",
            "{:,}".format(summary.masked_count),
            str(len(summary.sessions)),
        ])
    out_dir = os.path.join(config.data_dir, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "keylens-last%d.html" % days)
    body = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KeyLens 最近 %(n)d 天</title><style>%(style)s</style></head><body>
<div class="topbar"><div class="topbar-inner"><span class="logo">%(logo)s</span><span class="t">KeyLens 汇总报告</span>
  <span class="day">最近 %(n)d 天</span>
  <button type="button" class="theme-btn" id="themeBtn"><span id="themeBtnText">明亮</span></button></div></div>
<main>
<h1>最近 %(n)d 天按键汇总</h1>
<div class="meta"><span class="chip">生成时间 %(generated)s</span>
  <span class="chip">仅供本机自查使用</span></div>
%(table)s
<div class="foot"><b>说明：</b>汇总报告只统计每天的按键总量、活跃跨度、掩码按键与会话数，不包含任何按键内容；
明细请查看对应日期的日报。数据全部为本地文件，程序不含任何网络代码。</div>
</main>
%(theme_js)s</body></html>""" % {
        "n": days,
        "style": _STYLE,
        "logo": _logo_img(),
        "theme_js": _THEME_SCRIPT,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "table": _render_table(["日期", "按键总数", "活跃跨度", "掩码按键", "会话数"], rows),
    }
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


__all__ = ["build_html", "write_daily_report", "write_range_report", "_render_page"]
