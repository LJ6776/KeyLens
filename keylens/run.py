"""exe 打包专用入口。

PyInstaller 直接以脚本方式运行本文件（没有包上下文），所以这里用**绝对导入**
并手动补包路径，避开 ``main.py`` 里面向 `python main.py` 设计的 runpy 桥接。

日常开发仍用 ``python main.py``；只有打包时才以本文件为入口：

    pyinstaller --onefile --windowed --name KeyLens --icon app.ico --paths .. run.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keylens.main import main  # noqa: E402

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl+C 优雅退出：记录线程已在 main() 的 finally 里停止
        raise SystemExit(0)
