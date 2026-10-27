"""KeyLens 自检：不装钩子、不开窗口，纯逻辑与报告生成的端到端校验。

用法（在项目根目录下）：
    python -m keylens.selftest

它会在临时目录里建一个数据库，灌入模拟按键，跑一遍统计与报告生成，
并逐项断言。全部通过会打印 OK 并返回 0；任何问题都会打印 FAIL 明细并返回 1。
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import sys
import tempfile
import traceback
from typing import List, Tuple

from .core import keymap
from .core.db import Database, EventRecord, day_str

FAILURES: List[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print("  OK   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        FAILURES.append("%s %s" % (label, detail))


def section(title: str) -> None:
    print("\n== %s ==" % title)


# ---------------------------------------------------------------- 1. 键位解析


def test_keymap() -> None:
    section("键位解析与布局")
    cases: List[Tuple[int, int, bool, str]] = [
        (0x41, 0x1E, False, "A"),
        (0x1B, 0x01, False, "ESC"),
        (0x0D, 0x1C, False, "ENTER"),
        (0x25, 0x4B, True, "LEFT"),
        (0x2E, 0x53, True, "DELETE"),
        (0x20, 0x39, False, "SPACE"),
        (0x70, 0x00, False, "F1"),
        (0x2A, 0x2A, False, "L_SHIFT"),
        (0xA1, 0x36, False, "R_SHIFT"),
        (0x6A, 0x37, False, "NUMPAD_MULTIPLY"),
    ]
    for vk, scan, extended, want in cases:
        got = keymap.resolve(vk, scan, extended)
        check("resolve vk=%02X scan=%02X ext=%s -> %s" % (vk, scan, extended, want), got == want,
              "得到 %s" % got)

    missing: List[str] = []
    for block_title, rows in keymap.layout_blocks():
        for row in rows:
            for item in row:
                if not isinstance(item, tuple):
                    continue
                key = item[0]
                # 显示名：DISPLAY 里没有的键（字母、数字、F1…）直接用键名本身
                if not keymap.display(key):
                    missing.append("%s 无显示名" % key)
                if key not in keymap.key_index():
                    missing.append("%s 不在热力图索引" % key)
                if keymap.category(key) == "other":
                    missing.append("%s 未归类" % key)
    check("布局内每个键都有显示名/索引/分类", not missing, "; ".join(missing))

    keys = keymap.heatmap_keys()
    check("热力图键位无重复", len(keys) == len(set(keys)),
          "%d / %d" % (len(keys), len(set(keys))))
    check("掩码桶不进热力图", "MASKED" not in keys)


# ---------------------------------------------------------------- 2. 存储


def test_database(root: str) -> Tuple[Database, str]:
    section("SQLite 建库、写入与统计")
    path = os.path.join(root, "nested", "selftest.db")
    database = Database(path)
    database.initialize()
    check("嵌套目录自动创建", os.path.isdir(os.path.dirname(path)))

    base = 1700000000.0
    day = day_str(base)
    # 会话与事件落在同一天，否则 day_summary 查不到会话。
    remote_id = database.open_session(remote=True, remote_note="向日葵 测试", masked=True, day=day)
    local_id = database.open_session(remote=False, remote_note="", masked=True, day=day)

    records: List[EventRecord] = []
    for i in range(60):
        records.append(EventRecord(
            ts=base + i, session_id=remote_id, key_id=("A" if i % 2 else "MASKED"),
            category=keymap.category(("A" if i % 2 else "MASKED")), vk=0x41, scan=0x1E,
            flags=0, process="chrome.exe", title="测试窗口",
        ))
    for i in range(40):
        records.append(EventRecord(
            ts=base + 100 + i, session_id=local_id, key_id="ENTER",
            category="edit", vk=0x0D, scan=0x1C, flags=0, process="notepad.exe", title="无标题",
        ))
    inserted = database.insert_events(records)
    check("批量写入条数", inserted == 100, "写入 %d" % inserted)
    database.close_session(remote_id, 60)
    database.close_session(local_id, 40)

    summary = database.day_summary(day)
    check("当日总数", summary.key_count == 100, "得到 %d" % summary.key_count)
    check("掩码计数", summary.masked_count == 30, "得到 %d" % summary.masked_count)
    check("会话数", len(summary.sessions) == 2, "得到 %d" % len(summary.sessions))
    check("远程会话被标记", any(s.remote for s in summary.sessions))
    check("键位分布", summary.by_key.get("A") == 30 and summary.by_key.get("ENTER") == 40,
          str(summary.by_key))
    check("应用分布", summary.by_process.get("chrome.exe") == 60, str(summary.by_process))
    check("分时直方图长度为 24", len(summary.hourly) == 24)
    check("分时合计等于总数", sum(summary.hourly) == 100, "得到 %d" % sum(summary.hourly))
    check("totals 接口", database.totals() == (100, 2), str(database.totals()))
    check("available_days 含当天", day in database.available_days(), str(database.available_days()))

    # 保留期清理
    removed = database.purge_before("9999-01-01")
    check("purge_before 生效", removed == 100, "删除 %d" % removed)
    check("清理后为空", database.totals() == (0, 0), str(database.totals()))

    # 重新灌一遍，用于报告测试。会话要一起重建：上面那次 purge 把会话也清掉了，
    # 只补事件会让报告里的"会话明细 / 远程会话标注"整段消失。
    new_remote = database.open_session(remote=True, remote_note="向日葵 测试", masked=True, day=day)
    new_local = database.open_session(remote=False, remote_note="", masked=True, day=day)
    for rec in records:
        rec.session_id = new_remote if rec.session_id == remote_id else new_local
    database.insert_events(records)
    database.close_session(new_remote, 60)
    database.close_session(new_local, 40)
    return database, day


# ---------------------------------------------------------------- 2b. 复制 / 粘贴


def test_clipboard(database: Database, day: str, root: str) -> None:
    section("复制 / 粘贴记录")
    from .core import clipboard as clip_mod
    from .core.db import ClipRecord, Database, day_str
    from .core.tracker import Config

    check("Ctrl+V 判定为粘贴", clip_mod.is_paste_combo("V", True, False))
    check("Ctrl+Shift+V 判定为粘贴", clip_mod.is_paste_combo("V", True, True))
    check("单独的 V 不是粘贴", not clip_mod.is_paste_combo("V", False, False))
    check("Shift+Insert 判定为粘贴", clip_mod.is_paste_combo("INSERT", False, True))
    check("Ctrl+Insert 不算粘贴", not clip_mod.is_paste_combo("INSERT", True, False))
    check("Ctrl+C 判定为复制", clip_mod.is_copy_combo("C", True))
    check("单独的 C 不是复制", not clip_mod.is_copy_combo("C", False))

    base = 1700000000.0
    records: List[ClipRecord] = [
        ClipRecord(ts=base + 1, session_id=1, kind="copy", content="text",
                   formats="CF_UNICODETEXT", text_len=120, byte_len=240,
                   injected=0, process="Code.exe", title="a.py"),
        ClipRecord(ts=base + 2, session_id=1, kind="copy", content="image",
                   formats="CF_DIB", text_len=0, byte_len=4096,
                   injected=0, process="explorer.exe", title="图片"),
        ClipRecord(ts=base + 3, session_id=1, kind="paste", content="text",
                   formats="", text_len=120, byte_len=0,
                   injected=1, process="Code.exe", title="a.py"),
        ClipRecord(ts=base + 4, session_id=1, kind="paste", content="MASKED",
                   formats="", text_len=0, byte_len=0,
                   injected=0, process="chrome.exe", title="登录", masked=1),
    ]
    check("剪贴板写入条数", database.insert_clipboard(records) == 4)

    summary = database.clipboard_summary(day)
    check("复制次数统计", summary.copy_count == 2, str(summary.copy_count))
    check("粘贴次数统计", summary.paste_count == 2, str(summary.paste_count))
    check("远程注入粘贴被标记", summary.remote_count == 1, str(summary.remote_count))
    check("掩码记录被统计", summary.masked_count == 1, str(summary.masked_count))
    check("文本字符合计（掩码不计）", summary.text_chars == 240, str(summary.text_chars))
    check("内容类型分布", summary.by_content.get("text") == 2 and summary.by_content.get("image") == 1,
          str(summary.by_content))
    check("应用分布", summary.by_process.get("Code.exe") == 2, str(summary.by_process))
    check("剪贴板分时长度 24", len(summary.hourly) == 24)

    # 保留期清理必须把剪贴板记录一起清掉，否则会留下"动作数据"的尾巴。
    other = Database(os.path.join(root, "clip-purge.db"))
    other.initialize()
    other.insert_clipboard(records)
    other.purge_before("9999-01-01")
    check("剪贴板记录随保留期清理", other.clipboard_summary(day_str(base)).total == 0)

    # 配置：默认不保存正文
    cfg_dir = os.path.join(root, "clip-cfg")
    cfg = Config()
    cfg.data_dir = cfg_dir
    cfg.clipboard.store_text = True
    cfg.clipboard.max_text_chars = 40
    cfg.save()
    loaded = Config.load(cfg_dir)
    check("剪贴板配置往返",
          loaded.clipboard.store_text is True and loaded.clipboard.max_text_chars == 40,
          str(loaded.clipboard))
    check("默认不保存剪贴板正文", Config().clipboard.store_text is False)


# ---------------------------------------------------------------- 3. 报告


def test_report(database: Database, day: str, root: str) -> None:
    section("HTML 报告生成")
    from . import report
    from .core.tracker import Config

    config = Config()
    config.data_dir = root

    path = report.write_daily_report(database, config, day)
    check("日报文件存在", os.path.isfile(path), path)
    text = open(path, encoding="utf-8").read()
    check("日报体积合理", len(text) > 5000, "%d 字节" % len(text))

    # 最关键的契约：模板占位符与替换字典必须一一对应（少一个就 TypeError）
    for field in ("当日按键总数", "平均每分钟按键", "键盘覆盖率", "会话明细"):
        check("日报含 %s" % field, field in text)
    check("日报无未替换占位符", "%(" not in text)
    check("日报无残留格式化符", re.search(r"%[dsf]\b", text) is None, "存在残留")

    # 模板契约：占位符集合必须与替换字典键集合一致。
    # 这一条由上面的 write_daily_report 实际断言 —— 只要有任何占位符在字典里缺失，
    # 或者同名字段被覆盖（曾出现过 "sessions" 同时是 int 和 str 的 bug），
    # 格式化就会抛 TypeError，被 main() 捕获成一条 FAIL。
    check("模板占位符数量合理", len(set(re.findall(r"%\([A-Za-z_]+\)", report._PAGE_TEMPLATE))) >= 15,
          str(sorted(set(re.findall(r"%\([A-Za-z_]+\)", report._PAGE_TEMPLATE)))))

    # 键位排行李节里不允许出现掩码桶（h2 允许带锚点 id，用正则切分）
    ranking = re.split(r"<h2[^>]*>键位排行</h2>", text, maxsplit=1)
    check("键位排行存在", len(ranking) == 2)
    if len(ranking) == 2:
        body = re.split(r"<h2[^>]*>", ranking[1], maxsplit=1)[0]
        check("掩码桶未进入键位排行", "掩码（未记录具体键）" not in body)

    check("热力图渲染了 A 键", ">A<" in text or "A<" in text)
    check("远程会话标注存在", "远程会话" in text and "本地输入" in text)
    check("日报含复制/粘贴小节", "复制 / 粘贴" in text)

    range_path = report.write_range_report(database, config, 3)
    range_text = open(range_path, encoding="utf-8").read()
    check("汇总报告存在", os.path.isfile(range_path) and len(range_text) > 1000, range_path)
    check("汇总报告无未替换占位符", "%(" not in range_text)


# ---------------------------------------------------------------- 4. 配置与掩码


def test_config_and_masking(root: str) -> None:
    section("配置与隐私掩码")
    from .core.tracker import Config

    config = Config()
    config.data_dir = root
    config.privacy.password_apps = ["chrome.exe"]
    config.privacy.title_keywords = ["密码"]
    config.save()
    check("配置文件写出", os.path.isfile(config.config_path))

    loaded = Config.load(root)
    check("data_dir 往返", loaded.data_dir == root)
    # 掩码名单是"内置默认项 ∪ 用户配置"，所以自定义项在、默认项也必须在
    check("自定义掩码进程保留", "chrome.exe" in loaded.privacy.password_apps,
          str(loaded.privacy.password_apps))
    check("自定义关键字保留", "密码" in loaded.privacy.title_keywords,
          str(loaded.privacy.title_keywords))
    check("内置默认掩码项不被旧配置冲掉",
          "keepass.exe" in loaded.privacy.password_apps,
          "%s / %s" % (loaded.privacy.password_apps[:3], loaded.privacy.mask_processes[:3]))
    # 压缩 / 加密容器默认不并入（旧 config.json 里残留的同名项也会被收敛掉）
    check("压缩容器默认不掩码",
          "winrar.exe" not in loaded.privacy.mask_processes
          and not any("解压" in kw for kw in loaded.privacy.title_keywords),
          str(loaded.privacy.mask_processes))

    class FakeWindow:
        def __init__(self, process: str, title: str):
            self.process = process
            self.title = title

    import threading
    import types

    from .core.tracker import Tracker

    # 只测掩码判定：不启动线程、不装钩子、不建数据库。
    stub = types.SimpleNamespace(
        config=loaded,
        _lock=threading.RLock(),
        _masking_now=False,
    )
    should_mask = Tracker._should_mask

    check("密码管理器进程被掩码",
          should_mask(stub, FakeWindow("chrome.exe", "百度一下")) is True)
    check("标题关键字被掩码",
          should_mask(stub, FakeWindow("notepad.exe", "输入密码")) is True)
    check("普通窗口不掩码",
          should_mask(stub, FakeWindow("notepad.exe", "无标题 - 记事本")) is False)
    check("空标题不掩码", should_mask(stub, FakeWindow("notepad.exe", "")) is False)
    check("无前台窗口时不掩码", should_mask(stub, None) is False)

    # 解压 / 加密容器：默认整段不掩码（用户要看到自己在这里输入的口令）
    check("WinRAR 默认不掩码",
          should_mask(stub, FakeWindow("winrar.exe", "输入密码")) is False)
    check("好压默认不掩码",
          should_mask(stub, FakeWindow("haozip.exe", "解压到")) is False)
    check("7-Zip 默认不掩码",
          should_mask(stub, FakeWindow("7zfm.exe", "带密码解压")) is False)
    check("解压口令对话框标题被掩码",
          should_mask(stub, FakeWindow("explorer.exe", "输入解压密码")) is True)
    check("普通解压窗口不掩码",
          should_mask(stub, FakeWindow("explorer.exe", "文档")) is False)

    # strict_archive_mask = true：压缩/加密容器回到"整进程掩码"的严格模式
    loaded.privacy.strict_archive_mask = True
    check("严格模式下 WinRAR 被掩码",
          should_mask(stub, FakeWindow("winrar.exe", "输入密码")) is True)
    check("严格模式下 7-Zip 被掩码",
          should_mask(stub, FakeWindow("7zfm.exe", "带密码解压")) is True)
    loaded.privacy.strict_archive_mask = False

    # 剪贴板用更宽松的判定：浏览器里正常复制不该被连坐
    should_mask_clip = Tracker._should_mask_clipboard
    check("剪贴板：浏览器普通复制不掩码",
          should_mask_clip(stub, FakeWindow("chrome.exe", "某网页 - Chrome")) is False)
    check("剪贴板：标题含密码仍掩码",
          should_mask_clip(stub, FakeWindow("notepad.exe", "输入密码")) is True)
    check("剪贴板：密码管理器仍掩码",
          should_mask_clip(stub, FakeWindow("keepass.exe", "KeePass")) is True)
    check("剪贴板：压缩容器里的复制照常记录",
          should_mask_clip(stub, FakeWindow("haozip.exe", "输入解压密码 - 好压")) is False)
    check("剪贴板：WinRAR 里的复制照常记录",
          should_mask_clip(stub, FakeWindow("winrar.exe", "输入解压密码")) is False)
    loaded.privacy.clipboard_mask_strict = True
    check("剪贴板：严格模式下浏览器掩码",
          should_mask_clip(stub, FakeWindow("chrome.exe", "某网页")) is True)
    loaded.privacy.clipboard_mask_strict = False
    loaded.privacy.strict_archive_mask = True
    check("剪贴板：严格归档模式下压缩容器掩码",
          should_mask_clip(stub, FakeWindow("haozip.exe", "输入解压密码 - 好压")) is True)
    loaded.privacy.strict_archive_mask = False

    # 从内置名单里放开：明确写进 allow_unmask 的进程不再掩码
    loaded.privacy.allow_unmask = ["chrome.exe"]
    loaded.privacy.password_apps = [
        name for name in loaded.privacy.password_apps if name != "chrome.exe"
    ]
    check("allow_unmask 放开的进程不再掩码",
          should_mask(stub, FakeWindow("chrome.exe", "百度一下")) is False)

    loaded.privacy.enabled = False
    check("关闭掩码后一律不掩码",
          should_mask(stub, FakeWindow("chrome.exe", "输入密码")) is False)

    # 按键浮层事件：只带键位与修饰键，不带任何正文
    from .core.tracker import KeyPreview

    preview = KeyPreview(key="S", masked=False, injected=False,
                         ctrl=True, shift=False, alt=False, ts=0.0)
    check("浮层显示组合键", preview.label == "Ctrl + S", preview.label)
    full = KeyPreview(key="A", masked=False, injected=False,
                      ctrl=True, shift=True, alt=True, ts=0.0)
    check("浮层显示三键组合", full.label == "Ctrl + Shift + Alt + A", full.label)
    hidden = KeyPreview(key="S", masked=True, injected=False,
                        ctrl=False, shift=False, alt=False, ts=0.0)
    check("掩码时浮层不显示键名", "S" not in hidden.label, hidden.label)
    fields = set(KeyPreview.__dataclass_fields__)
    check("浮层事件不含正文字段", not {"text", "char", "content", "note"} & fields,
          str(sorted(fields)))
    check("记录器提供浮层回调接口", hasattr(Tracker, "set_key_observer"))
    check("记录器提供清除记录接口", hasattr(Tracker, "clear_records"))

    # 远程 / 监控软件识别：进程名命中返回友好显示名
    from .core import winapi

    check("向日葵进程识别为远程软件", winapi.REMOTE_APP_LABELS.get("sunloginclient") == "向日葵")
    check("VNC 系归入 VNC 标签", winapi.REMOTE_APP_LABELS.get("winvnc") == "VNC")
    check("ToDesk 识别为远程软件", "todesk" in winapi.REMOTE_PROCESS_NAMES)
    check("RustDesk 识别为远程软件", "rustdesk" in winapi.REMOTE_PROCESS_NAMES)
    check("TeamViewer 识别为远程软件", "teamviewer" in winapi.REMOTE_PROCESS_NAMES)
    check("解压软件不在远程名单", "winrar.exe" not in winapi.REMOTE_PROCESS_NAMES
          and "winrar" not in winapi.REMOTE_PROCESS_NAMES)
    # 报告里筛远程软件用的就是这两个集合
    check("报告筛选用到远程名单", hasattr(winapi, "REMOTE_APP_LABELS")
          and hasattr(winapi, "REMOTE_PROCESS_NAMES"))


# ---------------------------------------------------------------- 5. 预览生成


def test_preview(root: str) -> None:
    section("预览生成（示例报告 + 界面样张）")
    from . import preview

    out_dir = os.path.join(root, "previews")
    paths = preview.generate_previews(out_dir, total=2000)
    for name in ("report", "mockup"):
        path = paths[name]
        check("%s 文件存在" % name, os.path.isfile(path), path)
        if os.path.isfile(path):
            text = open(path, encoding="utf-8").read()
            check("%s 体积合理" % name, len(text) > 3000, "%d 字节" % len(text))
            check("%s 无未替换占位符" % name, "%(" not in text)

    report_text = open(paths["report"], encoding="utf-8").read()
    check("示例报告含远程会话标注", "远程会话" in report_text)
    check("示例报告含热力图", "键盘热力图" in report_text)
    check("示例报告含复制/粘贴小节", "复制 / 粘贴" in report_text)
    mockup_text = open(paths["mockup"], encoding="utf-8").read()
    check("界面样张含窗口标题", "KeyLens 按键记录器" in mockup_text)
    check("界面样张含掩码提示条", "命中隐私掩码" in mockup_text)
    check("界面样张含全部键盘分区",
          all(title in mockup_text for title, _rows in keymap.layout_blocks()))
    check("界面样张热力格子数量正确",
          mockup_text.count('class="key"') == len(keymap.heatmap_keys()),
          "%d / %d" % (mockup_text.count('class="key"'), len(keymap.heatmap_keys())))


# ---------------------------------------------------------------- 6. 打包前置


def _version_file_is_valid(path: str) -> Tuple[bool, str]:
    """验证 PyInstaller 版本文件：必须是**单个表达式**（它用 eval 读取，不是 exec）。

    stub 里如果有 import / raise / 赋值，PyInstaller 会在打包时报语法或求值错误。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        return False, "读取失败：%s" % exc
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return False, "语法错误：%s" % exc
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
        kinds = [type(node).__name__ for node in tree.body]
        return False, "不是单个表达式（发现 %s）——PyInstaller 用 eval 读取本文件" % ", ".join(kinds)
    call = tree.body[0].value
    if not isinstance(call, ast.Call) or getattr(call.func, "id", "") != "VSVersionInfo":
        return False, "顶层表达式不是 VSVersionInfo(...) 调用"
    return True, "OK"


def test_build_preflight(root: str) -> None:
    """打包前置检查：脚本引用的路径是否真实存在、版本文件是否合法、图标是否在位。

    这一节专防"文件被搬走 / 被换成占位文件，等跑打包才发现"的情况。
    """
    section("打包前置检查")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools_dir = os.path.join(project_root, "tools")
    pkg_dir = os.path.join(project_root, "keylens")

    candidates = [
        os.path.join(tools_dir, "make-exe.bat"),
        os.path.join(pkg_dir, "make-exe.bat"),
    ]
    existing = [p for p in candidates if os.path.isfile(p)]
    check("存在 make-exe.bat", bool(existing), str(candidates))
    if not existing:
        return

    preferred = os.path.join(tools_dir, "make-exe.bat")
    script = preferred if os.path.isfile(preferred) else existing[0]
    if script != preferred:
        check("优先使用 tools\\make-exe.bat", False, "只找到 %s" % script)

    try:
        with open(script, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        check("可读取打包脚本", False, str(exc))
        return

    for candidate in (os.path.join(tools_dir, "exe-version.py"),
                      os.path.join(pkg_dir, "exe-version.py")):
        if not os.path.isfile(candidate):
            continue
        ok, detail = _version_file_is_valid(candidate)
        check("版本文件 %s 合法" % os.path.relpath(candidate, project_root), ok, detail)

    referenced = re.findall(r"[\w\\.%-]+\.(?:ico|py|bat)", text)
    missing: List[str] = []
    for token in referenced:
        cleaned = token.strip("^\"' ").replace("/", os.sep)
        if cleaned.startswith(("--", "%")):
            continue
        if not os.path.exists(os.path.join(project_root, cleaned)):
            missing.append(cleaned)
    check("打包脚本引用的路径都存在", not missing, "; ".join(sorted(set(missing))))

    icon_in_tools = os.path.isfile(os.path.join(tools_dir, "app.ico"))
    icon_in_pkg = os.path.isfile(os.path.join(pkg_dir, "app.ico"))
    check("app.ico 至少存在于一个位置", icon_in_tools or icon_in_pkg,
          "tools=%s, keylens=%s" % (icon_in_tools, icon_in_pkg))
    if "tools\\app.ico" in text:
        check("脚本引用的 tools\\app.ico 存在", icon_in_tools,
              "tools\\app.ico 缺失，请把它放回 tools\\")

    check("使用 --onedir（避开 onefile 解压被拦）", "--onedir" in text)
    check("使用 --windowed", "--windowed" in text)
    check("显式 --paths 让 keylens 可被解析", "--paths" in text)
    check("入口是 keylens\\run.py", "keylens\\run.py" in text or "keylens/run.py" in text)
    _check_version_consistency(project_root, tools_dir, pkg_dir)


def _check_version_consistency(project_root: str, tools_dir: str, pkg_dir: str) -> None:
    """界面上的版本号（keylens.__version__）与 exe 文件属性里的版本必须一致。

    这两处不在同一个文件里，改版本号时漏掉一处，就会出现"关于里写 1.1、
    文件属性还是 1.0"这种对不上的情况——比完全没有版本号更让人困惑。
    """
    from . import __version__

    parts = __version__.split(".")
    digits: List[int] = []
    for part in parts:
        digits.append(int(part) if part.isdigit() else 0)
    while len(digits) < 4:
        digits.append(0)
    quad = "%d.%d.%d.%d" % tuple(digits[:4])
    check("keylens.__version__ 是三段数字", len(parts) == 3 and all(p.isdigit() for p in parts),
          __version__)

    found = ""
    for candidate in (os.path.join(tools_dir, "exe-version.py"),
                      os.path.join(pkg_dir, "exe-version.py")):
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        matched = re.search(r'StringStruct\(\s*"FileVersion"\s*,\s*"([^"]+)"\s*\)', body)
        if matched:
            found = matched.group(1)
            break
    check("exe 文件属性版本与界面版本一致", found == quad,
          "界面 %s(%s) / exe %s" % (__version__, quad, found or "未找到"))


def main() -> int:
    print("KeyLens 自检开始（不安装键盘钩子，不打开窗口）")
    print("Python %s，平台 %s" % (sys.version.split()[0], sys.platform))

    root = tempfile.mkdtemp(prefix="keylens-selftest-")
    try:
        test_keymap()
        database, day = test_database(root)
        test_clipboard(database, day, root)
        test_report(database, day, root)
        test_config_and_masking(root)
        test_preview(root)
        test_build_preflight(root)
    except Exception:
        print("\n自检过程中抛出异常：")
        traceback.print_exc()
        FAILURES.append("未捕获异常")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 60)
    if FAILURES:
        print("自检失败，共 %d 项：" % len(FAILURES))
        for item in FAILURES:
            print("  - %s" % item)
        return 1
    print("自检全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
