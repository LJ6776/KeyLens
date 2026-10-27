"""本地存储层：SQLite 建库、批量写入、统计查询、保留期清理。

隐私设计：
  * 数据只落在本地文件，代码里**没有任何网络调用**；
  * 表里存的是"哪个键被按了多少次 + 属于哪个应用/会话"，不存字符内容，
    也不提供任何把事件序列还原成文本的函数；
  * 命中隐私掩码的按键记为 ``MASKED`` 桶，只有次数、没有键位。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    ts         REAL    NOT NULL,      -- Unix 时间戳（秒，带小数）
    day        TEXT    NOT NULL,      -- 本地日期 YYYY-MM-DD，便于按天聚合
    session_id INTEGER NOT NULL,
    key_id     TEXT    NOT NULL,      -- 键位标识，如 A / ENTER / MASKED
    category   TEXT    NOT NULL,      -- 键位类别
    vk         INTEGER NOT NULL,
    scan       INTEGER NOT NULL,
    flags      INTEGER NOT NULL,      -- 1=扩展键 16=注入事件（远程注入时为 1）
    process    TEXT    NOT NULL,      -- 前台进程名，如 chrome.exe
    title      TEXT    NOT NULL       -- 前台窗口标题（可在配置里屏蔽）
);

CREATE INDEX IF NOT EXISTS idx_events_day        ON events(day);
CREATE INDEX IF NOT EXISTS idx_events_day_key    ON events(day, key_id);
CREATE INDEX IF NOT EXISTS idx_events_day_proc   ON events(day, process);
CREATE INDEX IF NOT EXISTS idx_events_session    ON events(session_id);

-- 复制 / 粘贴：只登记动作、内容类型与规模，默认不存正文（见 clipboard.note）
CREATE TABLE IF NOT EXISTS clipboard (
    id         INTEGER PRIMARY KEY,
    ts         REAL    NOT NULL,      -- Unix 时间戳（秒，带小数）
    day        TEXT    NOT NULL,
    session_id INTEGER NOT NULL,
    kind       TEXT    NOT NULL,      -- copy / paste
    content    TEXT    NOT NULL,      -- text / image / files / empty / other / MASKED
    formats    TEXT    NOT NULL,      -- 剪贴板格式名列表（掩码时为空）
    text_len   INTEGER NOT NULL,      -- 字符数（掩码时不量）
    byte_len   INTEGER NOT NULL,      -- 剪贴板数据字节数（估算）
    injected   INTEGER NOT NULL,      -- 1=触发按键是远程注入（向日葵等）
    process    TEXT    NOT NULL,      -- 当时前台进程
    title      TEXT    NOT NULL,
    masked     INTEGER NOT NULL DEFAULT 0,
    note       TEXT    NOT NULL DEFAULT ''   -- 只有开启 store_text 时才可能非空
);

CREATE INDEX IF NOT EXISTS idx_clip_day       ON clipboard(day);
CREATE INDEX IF NOT EXISTS idx_clip_day_kind  ON clipboard(day, kind);
CREATE INDEX IF NOT EXISTS idx_clip_session   ON clipboard(session_id);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    day         TEXT NOT NULL,
    remote      INTEGER NOT NULL DEFAULT 0,   -- 该时段是否检测到远程控制会话
    remote_note TEXT NOT NULL DEFAULT '',
    masked      INTEGER NOT NULL DEFAULT 0,   -- 该时段是否启用了隐私掩码
    label       TEXT NOT NULL DEFAULT '',     -- 你自己的备注，如"向日葵协助"
    key_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily (
    day          TEXT PRIMARY KEY,
    key_count    INTEGER NOT NULL DEFAULT 0,
    active_secs  INTEGER NOT NULL DEFAULT 0,
    remote_count INTEGER NOT NULL DEFAULT 0,
    masked_count INTEGER NOT NULL DEFAULT 0,
    first_ts     REAL,
    last_ts      REAL
);
"""


# ---------------------------------------------------------------- 路径


def default_data_dir() -> str:
    """数据目录：优先 ``%LOCALAPPDATA%\\KeyLens``，不可用时退回当前目录。"""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return os.path.join(base, "KeyLens")
    return os.path.join(os.getcwd(), "keylens-data")


# ---------------------------------------------------------------- 数据类


@dataclass
class Session:
    id: int
    started_at: float
    ended_at: Optional[float]
    day: str
    remote: bool
    remote_note: str
    masked: bool
    label: str
    key_count: int

    @property
    def duration(self) -> float:
        end = self.ended_at if self.ended_at else time.time()
        return max(0.0, end - self.started_at)


@dataclass
class EventRecord:
    ts: float
    session_id: int
    key_id: str
    category: str
    vk: int
    scan: int
    flags: int
    process: str
    title: str


@dataclass
class ClipRecord:
    """一次复制 / 粘贴动作（不含正文，除非显式开启保存）。"""

    ts: float
    session_id: int
    kind: str  # copy / paste
    content: str  # text / image / files / empty / other / MASKED
    formats: str
    text_len: int
    byte_len: int
    injected: int  # 1=远程注入
    process: str
    title: str
    masked: int = 0
    note: str = ""


@dataclass
class DaySummary:
    day: str
    key_count: int = 0
    by_key: Dict[str, int] = field(default_factory=dict)
    by_category: Dict[str, int] = field(default_factory=dict)
    by_process: Dict[str, int] = field(default_factory=dict)
    hourly: List[int] = field(default_factory=lambda: [0] * 24)
    sessions: List[Session] = field(default_factory=list)
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None

    @property
    def active_minutes(self) -> float:
        if not self.first_ts or not self.last_ts:
            return 0.0
        return max(0.0, (self.last_ts - self.first_ts) / 60.0)

    @property
    def masked_count(self) -> int:
        return self.by_key.get("MASKED", 0)

    @property
    def coverage(self) -> float:
        from . import keymap

        total = keymap.TOTAL_TRACKED_KEYS or 1
        used = sum(1 for k, v in self.by_key.items() if v > 0 and k != "MASKED")
        return min(1.0, used / total)


@dataclass
class ClipSummary:
    """一天的复制 / 粘贴概况。"""

    day: str = ""
    total: int = 0
    copy_count: int = 0
    paste_count: int = 0
    masked_count: int = 0
    remote_count: int = 0  # 由远程注入触发的粘贴次数
    text_chars: int = 0  # 涉及文本的总字符数
    by_content: Dict[str, int] = field(default_factory=dict)
    by_process: Dict[str, int] = field(default_factory=dict)
    hourly: List[int] = field(default_factory=lambda: [0] * 24)


def day_str(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time()).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 数据库


class Database:
    """SQLite 封装。

    ``Database`` 本身只持有路径；每个线程通过 :meth:`connect` 取自己的连接
    （SQLite 连接不跨线程共用）。
    """

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.path.join(default_data_dir(), "keylens.db")
        self.path = path
        self._init_lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # -- 连接 -----------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=8000")
        return conn

    def initialize(self) -> None:
        with self._init_lock:
            conn = self.connect()
            try:
                conn.executescript(_SCHEMA)
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
                    ("created_at", str(time.time())),
                )
                conn.commit()
            finally:
                conn.close()

    # -- 会话 -----------------------------------------------------

    def open_session(self, remote: bool, remote_note: str, masked: bool, label: str = "",
                     day: Optional[str] = None) -> int:
        conn = self.connect()
        try:
            cur = conn.execute(
                "INSERT INTO sessions(started_at, day, remote, remote_note, masked, label)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (time.time(), day or day_str(), 1 if remote else 0, remote_note,
                 1 if masked else 0, label),
            )
            session_id = int(cur.lastrowid)
            conn.commit()
            return session_id
        finally:
            conn.close()

    def update_session(self, session_id: int, **fields) -> None:
        allowed = {"ended_at", "remote", "remote_note", "masked", "label", "key_count"}
        sets = []
        params: List[object] = []
        for name, value in fields.items():
            if name not in allowed:
                continue
            sets.append("%s = ?" % name)
            params.append(value)
        if not sets:
            return
        params.append(session_id)
        conn = self.connect()
        try:
            conn.execute("UPDATE sessions SET %s WHERE id = ?" % ", ".join(sets), params)
            conn.commit()
        finally:
            conn.close()

    def close_session(self, session_id: int, key_count: int) -> None:
        self.update_session(session_id, ended_at=time.time(), key_count=key_count)

    def recent_sessions(self, limit: int = 20) -> List[Session]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_session(r) for r in rows]

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=int(row["id"]),
            started_at=float(row["started_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
            day=row["day"],
            remote=bool(row["remote"]),
            remote_note=row["remote_note"] or "",
            masked=bool(row["masked"]),
            label=row["label"] or "",
            key_count=int(row["key_count"] or 0),
        )

    # -- 写入 -----------------------------------------------------

    def insert_events(self, records: Sequence[EventRecord], day: Optional[str] = None) -> int:
        if not records:
            return 0
        rows = [
            (
                rec.ts,
                day or day_str(rec.ts),
                rec.session_id,
                rec.key_id,
                rec.category,
                rec.vk,
                rec.scan,
                rec.flags,
                rec.process,
                rec.title,
            )
            for rec in records
        ]
        conn = self.connect()
        try:
            conn.executemany(
                "INSERT INTO events(ts, day, session_id, key_id, category, vk, scan, flags, process, title)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
        return len(rows)

    # -- 查询 -----------------------------------------------------

    def day_summary(self, day: str) -> DaySummary:
        summary = DaySummary(day=day)
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts"
                " FROM events WHERE day = ?",
                (day,),
            ).fetchone()
            summary.key_count = int(row["n"] or 0)
            summary.first_ts = float(row["first_ts"]) if row["first_ts"] else None
            summary.last_ts = float(row["last_ts"]) if row["last_ts"] else None

            for r in conn.execute(
                "SELECT key_id, COUNT(*) AS n FROM events WHERE day = ?"
                " GROUP BY key_id ORDER BY n DESC",
                (day,),
            ):
                summary.by_key[r["key_id"]] = int(r["n"])

            for r in conn.execute(
                "SELECT category, COUNT(*) AS n FROM events WHERE day = ?"
                " GROUP BY category ORDER BY n DESC",
                (day,),
            ):
                summary.by_category[r["category"]] = int(r["n"])

            for r in conn.execute(
                "SELECT process, COUNT(*) AS n FROM events WHERE day = ?"
                " GROUP BY process ORDER BY n DESC LIMIT 12",
                (day,),
            ):
                summary.by_process[r["process"] or "(未知)"] = int(r["n"])

            # SQLite 自带 strftime，按本地时区拆小时。
            for r in conn.execute(
                "SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS h,"
                " COUNT(*) AS n FROM events WHERE day = ? GROUP BY h",
                (day,),
            ):
                hour = int(r["h"])
                if 0 <= hour < 24:
                    summary.hourly[hour] = int(r["n"])

            for r in conn.execute(
                "SELECT * FROM sessions WHERE day = ? ORDER BY id", (day,)
            ):
                summary.sessions.append(self._row_to_session(r))
        finally:
            conn.close()
        return summary

    def insert_clipboard(self, records: Sequence[ClipRecord], day: Optional[str] = None) -> int:
        if not records:
            return 0
        rows = [
            (
                rec.ts,
                day or day_str(rec.ts),
                rec.session_id,
                rec.kind,
                rec.content,
                rec.formats,
                rec.text_len,
                rec.byte_len,
                rec.injected,
                rec.process,
                rec.title,
                rec.masked,
                rec.note,
            )
            for rec in records
        ]
        conn = self.connect()
        try:
            conn.executemany(
                "INSERT INTO clipboard(ts, day, session_id, kind, content, formats, text_len,"
                " byte_len, injected, process, title, masked, note)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
        return len(rows)

    def clipboard_summary(self, day: str) -> ClipSummary:
        summary = ClipSummary(day=day)
        conn = self.connect()
        try:
            for r in conn.execute(
                "SELECT kind, COUNT(*) AS n, COALESCE(SUM(text_len), 0) AS chars,"
                " COALESCE(SUM(injected), 0) AS inj FROM clipboard WHERE day = ? GROUP BY kind",
                (day,),
            ):
                count = int(r["n"])
                chars = int(r["chars"])
                summary.total += count
                summary.text_chars += chars
                summary.remote_count += int(r["inj"])
                if r["kind"] == "paste":
                    summary.paste_count = count
                else:
                    summary.copy_count += count

            for r in conn.execute(
                "SELECT COUNT(*) AS n FROM clipboard WHERE day = ? AND masked = 1", (day,)
            ):
                summary.masked_count = int(r["n"] or 0)

            for r in conn.execute(
                "SELECT content, COUNT(*) AS n FROM clipboard WHERE day = ?"
                " GROUP BY content ORDER BY n DESC",
                (day,),
            ):
                summary.by_content[r["content"] or "(未知)"] = int(r["n"])

            for r in conn.execute(
                "SELECT process, COUNT(*) AS n FROM clipboard WHERE day = ?"
                " GROUP BY process ORDER BY n DESC LIMIT 12",
                (day,),
            ):
                summary.by_process[r["process"] or "(未知)"] = int(r["n"])

            for r in conn.execute(
                "SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS h,"
                " COUNT(*) AS n FROM clipboard WHERE day = ? GROUP BY h",
                (day,),
            ):
                hour = int(r["h"])
                if 0 <= hour < 24:
                    summary.hourly[hour] = int(r["n"])
        finally:
            conn.close()
        return summary

    def totals(self) -> Tuple[int, int]:
        """返回 (全部事件数, 会话数)。"""
        conn = self.connect()
        try:
            events = int(conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] or 0)
            sessions = int(conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] or 0)
        finally:
            conn.close()
        return events, sessions

    def db_size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def available_days(self, limit: int = 60) -> List[str]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT day FROM events GROUP BY day ORDER BY day DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
        return [r["day"] for r in rows]

    # -- 维护 -----------------------------------------------------

    def purge_before(self, cutoff_day: str) -> int:
        """删除指定日期之前的全部明细与（已无明细的）会话。"""
        conn = self.connect()
        try:
            cur = conn.execute("DELETE FROM events WHERE day < ?", (cutoff_day,))
            removed = int(cur.rowcount or 0)
            conn.execute("DELETE FROM clipboard WHERE day < ?", (cutoff_day,))
            conn.execute(
                "DELETE FROM sessions WHERE day < ? AND id NOT IN (SELECT DISTINCT session_id FROM events)",
                (cutoff_day,),
            )
            conn.execute("DELETE FROM daily WHERE day < ?", (cutoff_day,))
            conn.commit()
        finally:
            conn.close()
        return removed

    def purge_all(self) -> None:
        conn = self.connect()
        try:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM clipboard")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM daily")
            conn.commit()
            # VACUUM 必须在事务之外执行，所以放 commit 之后
            conn.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()

    def checkpoint(self) -> None:
        conn = self.connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()


__all__ = [
    "Database",
    "EventRecord",
    "ClipRecord",
    "Session",
    "DaySummary",
    "ClipSummary",
    "day_str",
    "default_data_dir",
]
