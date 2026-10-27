"""报告入口（薄封装）。

真正的日报实现位于 :mod:`keylens.report`，本文件只是包一层转发，
避免出现"两套报告代码"。支持两种用法：

    python report_module.py            # 直接跑：自动补包路径后生成今天的报告
    python -m keylens.report_module    # 以包方式运行

推荐还是用项目自带入口：

    python main.py --report today
"""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    # 直接以脚本运行（python report_module.py / 双击）时没有包上下文，
    # 下面的相对导入会失败。把父目录塞进 sys.path 后，用包的方式重跑本模块，
    # 行为与 `python -m keylens.report_module` 完全一致。
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import runpy

    _ = runpy.run_module("keylens.report_module", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

from .core import db as db_module
from .core.tracker import Config
from .report import build_html, write_daily_report, write_range_report

__all__ = ["build_html", "write_daily_report", "write_range_report", "main"]


def main(argv: list[str] | None = None) -> int:
    """生成指定日期（默认今天）的 HTML 报告，并打印文件路径。"""
    args = list(sys.argv[1:] if argv is None else argv)
    day = args[0] if args else None

    config = Config.load()
    os.makedirs(config.data_dir, exist_ok=True)

    database = db_module.Database(config.db_path)
    database.initialize()

    path = write_daily_report(database, config, day)
    print("报告已生成：%s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
