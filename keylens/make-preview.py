"""把 KeyLens 的示例报告与界面样张生成到工作目录，供直接双击预览。

在项目根目录执行：
    python keylens\\make-preview.py

它只调用 :mod:`keylens.preview`：示例数据写进临时数据库、用完即删，
不会读取也不会修改你真实的 ``%LOCALAPPDATA%\\KeyLens\\keylens.db``。
"""

from __future__ import annotations

import os
import sys

# 允许 `python keylens\make-preview.py` 直接运行（不必先 cd 到项目根目录）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keylens import preview  # noqa: E402


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "previews")
    paths = preview.build_html_preview(out_dir, total=18000)
    print("示例数据只写进临时数据库，已随生成过程删除，未触碰真实记录。")
    print("示例报告： %s" % paths["report"])
    print("界面样张： %s" % paths["mockup"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
