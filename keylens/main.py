"""KeyLens 入口。

用法（两种都行，推荐前一种）：
    python -m keylens.main                   # 打开常驻可见的监控窗口并开始记录
    python main.py                           # 直接跑脚本也支持，下面那段引导会自动补包路径

常用参数：
    --paused                                 # 启动后先暂停，不记录
    --report                                 # 只生成今天的 HTML 报告并打开数据目录
    --report 2024-05-01                      # 生成指定日期的报告
    --range 7                                # 最近 7 天汇总报告
    --self-test                              # 跑自检（不装钩子、不开窗口）
    --osd                                    # 启动时打开按键浮层（实时显示按键，不落库）
    --wipe                                   # 删除全部本地记录（需二次确认）
"""

from __future__ import annotations

# argparse 的 Namespace 属性在严格模式下会被推断成 Any（reportAny），
# 而 add_argument 的返回值按惯例不使用（reportUnusedCallResult）。
# 这两条是 basedpyright 的严格扩展规则，在命令行解析场景属于噪音，故只在本文档关闭。
# pyright: reportUnusedCallResult=false, reportAny=false

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Optional

if __package__ in (None, ""):
    # 直接以脚本方式运行（python main.py / 双击）时没有包上下文，
    # 下面的相对导入会失败。这里把父目录塞进 sys.path，
    # 再用包的方式重新执行同一个入口，行为与 `python -m keylens.main` 完全一致。
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import runpy

    _ = runpy.run_module("keylens.main", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

from .core import db as db_module
from .core.tracker import Config, Tracker


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="keylens", description="本机自用按键记录器（可见窗口，无网络）")
    parser.add_argument("--data-dir", default=None, help="数据目录，默认 %%LOCALAPPDATA%%\\KeyLens")
    parser.add_argument("--paused", action="store_true", help="启动后先暂停记录")
    parser.add_argument("--report", nargs="?", const="today", default=None,
                        help="生成指定日期的 HTML 报告（默认今天），不打开界面")
    parser.add_argument("--range", dest="range_days", type=int, default=None,
                        help="生成最近 N 天的汇总报告")
    parser.add_argument("--wipe", action="store_true", help="删除全部本地记录")
    parser.add_argument("--purge-days", type=int, default=None, help="删除 N 天前的记录")
    parser.add_argument("--self-test", action="store_true",
                        help="跑自检（不装钩子、不开窗口），验证统计与报告链路")
    parser.add_argument("--osd", action="store_true",
                        help="启动时打开按键浮层（实时显示按了哪个键，不落库；密码框自动隐藏）")
    parser.add_argument("--demo", nargs="?", const="", default=None, metavar="DIR",
                        help="生成示例报告与界面样张到 DIR（默认 .\\keylens-previews），不记录真实按键")
    parser.add_argument("--demo-total", type=int, default=18000,
                        help="示例数据的总按键数，默认 18000")
    return parser.parse_args(argv)


def open_database(config: Config) -> db_module.Database:
    """打开（必要时创建）数据库并建表。"""
    database = db_module.Database(config.db_path)
    database.initialize()
    return database


def _show_startup_error(config: Config, exc: Exception) -> None:
    """数据库打不开时的友好提示（窗口模式没有控制台，必须用弹窗）。"""
    message = (
        "无法打开数据库：\n%s\n\n错误：%s\n\n"
        "最常见原因：杀毒/安全软件拦截了新打包的 exe 的文件写入。\n"
        "注意：托盘「退出」往往只是关闭界面，实时防护服务（如电脑管家的 QQPCRTP）\n"
        "仍在拦截——本程序连建目录都会被拒绝访问。\n"
        "解决办法（任选其一）：\n"
        "  1. 把本程序所在整个目录加入杀毒软件的信任区（白名单），然后重新运行；\n"
        "  2. 彻底关闭实时防护（不是只退托盘图标）后重新运行；\n"
        "  3. 使用项目里的「启动KeyLens.bat」直接用 Python 启动，不经过 exe。"
    ) % (config.db_path, exc)
    try:
        import tkinter as _tk
        import tkinter.messagebox as mb

        _root: Optional[_tk.Tk] = None
        try:
            _root = _tk.Tk()
            _root.withdraw()
        except Exception:
            pass
        if _root is not None:
            mb.showerror("KeyLens 启动失败", message, parent=_root)
            _root.destroy()
        else:
            mb.showerror("KeyLens 启动失败", message)
    except Exception:
        pass
    try:
        print(message)
    except Exception:
        pass


def _confirm(prompt: str) -> bool:
    """危险操作二次确认。

    打包成 ``--windowed`` exe 后没有控制台，``sys.stdin`` 是 None，
    直接 ``input()`` 会抛 ``RuntimeError: lost sys.stdin``。
    所以有真终端时用终端，否则退回 tkinter 弹窗。
    """
    if sys.stdin is not None:
        try:
            if sys.stdin.isatty():
                return input(prompt).strip().lower() == "yes"
        except (EOFError, RuntimeError, ValueError):
            return False
    try:
        import tkinter.messagebox as messagebox

        return bool(messagebox.askyesno("KeyLens", prompt))
    except Exception:
        return False


def _force_utf8_output() -> None:
    """Windows 控制台/管道下让中文输出不乱码：重配置流编码并把控制台切到 65001。

    窗口模式 exe 的 stdout 为 None、无控制台句柄，相关调用都被 try/except 兜住。
    """
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_output()
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.self_test:
        from .selftest import main as run_selftest

        return run_selftest()

    if args.demo is not None:
        from . import preview

        out_dir = args.demo or None
        paths = preview.build_html_preview(out_dir, total=max(100, args.demo_total), open_files=False)
        print("示例数据只写进了临时数据库，已随生成过程删除，未触碰你的真实记录。")
        print("示例报告：%s" % paths["report"])
        print("界面样张：%s" % paths["mockup"])
        return 0

    config = Config.load(args.data_dir)
    if args.paused:
        config.start_paused = True

    try:
        os.makedirs(config.data_dir, exist_ok=True)
        if not os.path.isfile(config.config_path):
            config.save()
        database = open_database(config)
    except Exception as exc:
        # 打不开数据库最常见的原因是杀毒软件（360/电脑管家等）拦截了
        # 新打包的 exe 的文件写入。给出明确指引，而不是甩一个堆栈弹窗。
        _show_startup_error(config, exc)
        return 1

    # ---- 维护类命令（不进界面） ----
    if args.wipe:
        if _confirm("将删除 %s 中的全部按键记录，确认继续？" % config.db_path):
            database.purge_all()
            print("已清空全部记录。")
        else:
            print("已取消。")
        return 0

    if args.purge_days is not None:
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(days=max(0, args.purge_days))).strftime("%Y-%m-%d")
        removed = database.purge_before(cutoff)
        database.checkpoint()
        print("已删除 %s 之前的 %d 条记录。" % (cutoff, removed))
        return 0

    from . import report as report_module

    if args.report is not None:
        day = args.report
        if day in ("today", "", None):
            day = db_module.day_str()
        path = report_module.write_daily_report(database, config, day)
        print("报告已生成：%s" % path)
        return 0

    if args.range_days is not None:
        path = report_module.write_range_report(database, config, max(1, args.range_days))
        print("汇总报告已生成：%s" % path)
        return 0

    # ---- 正常启动 ----
    if not sys.platform.startswith("win"):
        print("KeyLens 依赖 Windows 的 WH_KEYBOARD_LL 输入钩子，当前系统不受支持。", file=sys.stderr)
        return 2

    if config.retention_days and config.retention_days > 0:
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(days=config.retention_days)).strftime("%Y-%m-%d")
        database.purge_before(cutoff)

    record = Tracker(config)
    hook_ok = record.start()

    try:
        from .ui.app import KeyLensApp
    except Exception as exc:
        record.stop()
        print("无法加载界面（需要 tkinter）：%s" % exc, file=sys.stderr)
        return 3

    app = KeyLensApp(record, config, report_module=report_module, show_overlay=args.osd)
    if not hook_ok:
        app.status_label.configure(
            text="键盘钩子安装失败：%s。请尝试以管理员身份运行，或检查安全软件拦截。" % record.pause_reason
        )
    try:
        app.run()
    finally:
        record.stop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl+C 优雅退出：记录线程已在 main() 的 finally 里停止，这里安静退出即可
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        # 任何未预期异常都不能"无声闪退"：pythonw 下没有控制台，
        # 必须把完整堆栈弹窗展示出来，否则只能看到窗口一闪而过
        import traceback

        err = traceback.format_exc()
        try:
            sys.stderr.write(err + "\n")
        except Exception:
            pass  # pythonw 下 stderr 可能为 None
        try:
            import tkinter as _tk
            import tkinter.messagebox as _mb

            _root = _tk.Tk()
            _root.withdraw()
            _mb.showerror("KeyLens 启动失败", err[-3000:], parent=_root)
            _root.destroy()
        except Exception:
            pass
        raise SystemExit(1)
