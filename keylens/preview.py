"""预览生成：用生产代码渲染一份"示例报告 + 界面样张"到本地 HTML。

设计要点：
  * 示例数据只写进**临时目录**的临时数据库，绝不碰你真实的 ``keylens.db``；
  * 报告由 :func:`keylens.report.write_daily_report` 这个**真实函数**渲染，
    所以预览和实际生成的结果不会走样（这也是它比手写样张更可信的原因）；
  * 全程不安装键盘钩子、不开窗口、不写任何网络内容。
"""

from __future__ import annotations

import os
import random
import shutil
import tempfile
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .core import keymap
from .core.db import ClipRecord, Database, EventRecord, day_str
from .core.tracker import Config
from .ui import mockup

# 示例数据的时间跨度：今天 09:00 到 16:00，中间有午休低谷。
_START_HOUR = 9
_SPAN_HOURS = 7
_SEED = 20240501

# 每个小时的忙碌权重（0-1），用来造出真实的"上午忙、中午停、下午再忙"曲线。
_HOUR_WEIGHTS = [0.55, 0.85, 1.0, 0.9, 0.35, 0.15, 0.75, 0.95]

# 落在隐私掩码应用里的比例（近似：浏览器里打字一般占相当一部分）。
_MASKED_RATIO = 0.12

# 示例里用到的"前台应用"，与 mockup 的分布保持一致。
_APPS: List[Tuple[str, float]] = [
    ("chrome.exe", 0.42),
    ("Code.exe", 0.26),
    ("WINWORD.EXE", 0.16),
    ("explorer.exe", 0.08),
    ("wechat.exe", 0.05),
    ("notepad.exe", 0.03),
]

# 示例剪贴板内容类型分布：大部分是文本，偶尔文件 / 图片。
_CLIP_CONTENT: List[Tuple[str, float]] = [
    ("text", 0.72),
    ("files", 0.12),
    ("image", 0.10),
    ("empty", 0.06),
]

_CLIP_FORMATS: Dict[str, str] = {
    "text": "CF_UNICODETEXT",
    "files": "CF_HDROP",
    "image": "CF_DIB",
}

_APP_TITLES: Dict[str, str] = {
    "chrome.exe": "示例页面 - Google Chrome",
    "Code.exe": "preview.py - KeyLens - Visual Studio Code",
    "WINWORD.EXE": "示例文档.docx - Word",
    "explorer.exe": "keylens 预览",
    "wechat.exe": "微信",
    "notepad.exe": "无标题 - 记事本",
}


def _hourly_targets(total: int) -> List[int]:
    """把总数按时段权重摊到每个小时，并保留一点抖动。"""
    weights = _HOUR_WEIGHTS[:_SPAN_HOURS]
    scale = sum(weights)
    rng = random.Random(_SEED)
    targets: List[int] = []
    for weight in weights:
        jitter = rng.uniform(0.9, 1.1)
        targets.append(max(1, int(total * weight / scale * jitter)))
    # 修正四舍五入带来的偏差，全部加到最忙的那个小时上。
    drift = total - sum(targets)
    busiest = max(range(len(targets)), key=lambda i: targets[i])
    targets[busiest] = max(1, targets[busiest] + drift)
    return targets


def _pick_app(rng: random.Random) -> str:
    roll = rng.random()
    cumulative = 0.0
    for name, share in _APPS:
        cumulative += share
        if roll <= cumulative:
            return name
    return _APPS[-1][0]


def _pick_content(rng: random.Random) -> str:
    roll = rng.random()
    cumulative = 0.0
    for name, share in _CLIP_CONTENT:
        cumulative += share
        if roll <= cumulative:
            return name
    return "text"


def _clipboard_samples(
    rng: random.Random, database: Database, start_ts: float, remote_from: float,
    local_id: int, remote_id: int, count: int,
) -> None:
    """灌一批复制 / 粘贴示例（只有类型与长度，没有任何正文）。"""
    records: List[ClipRecord] = []
    for _ in range(count):
        app = _pick_app(rng)
        ts = start_ts + rng.uniform(0.0, _SPAN_HOURS * 3600.0)
        session_id = remote_id if ts >= remote_from else local_id
        is_paste = rng.random() < 0.42
        masked = app == "chrome.exe" and rng.random() < 0.3
        content = "MASKED" if masked else _pick_content(rng)
        text_len = 0 if (masked or content != "text") else rng.randint(4, 900)
        records.append(ClipRecord(
            ts=ts,
            session_id=session_id,
            kind="paste" if is_paste else "copy",
            content=content,
            formats="" if masked else _CLIP_FORMATS.get(content, ""),
            text_len=text_len,
            byte_len=0 if masked else max(64, text_len * 2),
            injected=1 if (is_paste and not masked and rng.random() < 0.25) else 0,
            process=app,
            title=_APP_TITLES.get(app, app),
            masked=1 if masked else 0,
        ))
    records.sort(key=lambda rec: rec.ts)
    database.insert_clipboard(records)


def build_sample_database(db_path: str, total: int = 18000) -> Database:
    """在 ``db_path`` 建一个灌好示例数据的数据库，返回它。

    示例数据本身**不含任何真实输入**：键位来自布局里的常见键，
    命中掩码的那部分统一记为 ``MASKED``，窗口标题都是"示例"字样。
    """
    database = Database(db_path)
    database.initialize()

    rng = random.Random(_SEED)
    counts = mockup.sample_counts()
    keys = list(counts.keys())
    weights = [counts[k] for k in keys]

    today = datetime.now().replace(minute=0, second=0, microsecond=0)
    start = today.replace(hour=_START_HOUR)
    targets = _hourly_targets(total)

    # 前 5 小时为"本地输入"，后 2 小时为"远程会话"（模拟同事用向日葵协助）。
    split_index = max(1, _SPAN_HOURS - 2)
    local_total = sum(targets[:split_index])
    remote_total = sum(targets[split_index:])

    local_id = database.open_session(remote=False, remote_note="", masked=True)
    remote_id = database.open_session(
        remote=True, remote_note="向日葵远程控制 - 示例会话", masked=True
    )

    records: List[EventRecord] = []
    for hour_index, target in enumerate(targets):
        session_id = local_id if hour_index < split_index else remote_id
        hour_start = start.timestamp() + hour_index * 3600.0
        for _ in range(target):
            key_id = rng.choices(keys, weights=weights, k=1)[0]
            app = _pick_app(rng)
            if app in ("chrome.exe",) and rng.random() < _MASKED_RATIO:
                key_id = "MASKED"
            ts = hour_start + rng.uniform(0.0, 3599.0)
            records.append(EventRecord(
                ts=ts,
                session_id=session_id,
                key_id=key_id,
                category=keymap.category(key_id),
                vk=0,
                scan=0,
                flags=0,
                process=app,
                title=_APP_TITLES.get(app, app),
            ))

    records.sort(key=lambda record: record.ts)
    database.insert_events(records)
    database.close_session(local_id, local_total)
    database.close_session(remote_id, remote_total)

    _clipboard_samples(
        rng, database, start.timestamp(), start.timestamp() + split_index * 3600.0,
        local_id, remote_id, max(12, total // 900),
    )
    return database


def generate_previews(out_dir: str, total: int = 18000) -> Dict[str, str]:
    """生成预览文件，返回 ``{"report": 路径, "mockup": 路径}``。"""
    from . import report as report_module

    root = tempfile.mkdtemp(prefix="keylens-preview-")
    try:
        database = build_sample_database(os.path.join(root, "preview.db"), total=total)
        config = Config()
        config.data_dir = root

        os.makedirs(out_dir, exist_ok=True)
        day = day_str(time.time())

        rendered = report_module.write_daily_report(database, config, day)
        report_path = os.path.join(out_dir, "sample-report.html")
        shutil.copyfile(rendered, report_path)

        mockup_path = os.path.join(out_dir, "window-mockup.html")
        with open(mockup_path, "w", encoding="utf-8") as fh:
            fh.write(mockup.render_mockup(masking_now=True))

        return {"report": report_path, "mockup": mockup_path}
    finally:
        # 临时数据库随报告一起删掉：预览不需要留任何按键数据。
        shutil.rmtree(root, ignore_errors=True)


def open_in_browser(path: str) -> bool:
    """尽力用默认程序打开预览文件；失败返回 False（不抛异常）。"""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)  # type: ignore[attr-defined]
            return True
    except Exception:
        pass
    return False


def build_html_preview(out_dir: Optional[str] = None, total: int = 18000,
                       open_files: bool = False) -> Dict[str, str]:
    """对外入口：生成预览（默认写到当前目录的 ``keylens-previews``）。"""
    target = out_dir or os.path.join(os.getcwd(), "keylens-previews")
    paths = generate_previews(target, total=total)
    if open_files:
        open_in_browser(paths["report"])
        open_in_browser(paths["mockup"])
    return paths


__all__ = [
    "generate_previews",
    "build_html_preview",
    "build_sample_database",
    "open_in_browser",
]
