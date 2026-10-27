"""KeyLens 无窗口启动器（.pyw 版）。

双击本文件即可运行：Windows 把 .pyw 关联到 pythonw.exe（GUI 子系统，没有控制台），
而 .pyw 不在 Windows 的“潜在不安全文件类型”清单里，所以不会出现「打开文件 - 安全警告」弹窗。

等价于在 keylens 目录里执行： pythonw.exe -m keylens.main
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))  # ...\keylens
os.chdir(HERE)  # python 包 keylens 就在本目录下
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from keylens.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
