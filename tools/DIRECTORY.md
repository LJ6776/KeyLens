# KeyLens 目录清单

> 生成依据：实际的 `glob` 结果与逐文件 `grep`，不是凭记忆写的。
> 最后一次整理：把构建/签名资产从 `keylens\` 包根移进 `tools\`。

## 目标结构

```
<项目根目录>\                ← 项目根（也是 PyInstaller 的 --paths 基准）
├── .gitignore                        禁止提交生成物与签名私钥
├── 启动KeyLens.pyw                   日常启动（.pyw 关联 pythonw：无窗口、无安全警告）
├── 启动KeyLens.bat                   备用启动（会先弹「打开文件-安全警告」再开控制台）
├── README.md                         工作目录总览
│
├── keylens\                          纯 Python 包，只放会被 import 的东西
│   ├── __init__.py
│   ├── main.py                       入口与命令行（自带 runpy 桥接，支持直接跑脚本）
│   ├── report.py                     报告渲染（_PAGE_TEMPLATE + _render_page）
│   ├── report_module.py              报告模块的兼容转发层
│   ├── run.py                        PyInstaller 打包入口
│   ├── selftest.py                   自检（不装钩子、不开窗口）
│   ├── preview.py                    示例报告 / 界面样张生成
│   ├── make-preview.py               把预览写到 keylens\previews\
│   ├── README.md                     项目主文档
│   ├── core\                         引擎层：采集、存储、统计
│   │   ├── keymap.py                 扫描码 → 键位标识、分类、键盘布局
│   │   ├── winapi.py                 钩子 / 前台窗口 / 修饰键 / 空闲 / 远控探测
│   │   ├── clipboard.py              剪贴板监听、内容类型与规模解析
│   │   ├── db.py                     SQLite 建库、批量写入、统计、保留期
│   │   ├── tracker.py                采集调度、掩码、会话、实时计数
│   │   └── tray.py                   托盘图标 + 全局热键（纯 ctypes）
│   ├── ui\                           表现层
│   │   ├── app.py                    主窗口 + 热力图 + 设置
│   │   ├── osd.py                    按键浮层（只走内存，不落库）
│   │   ├── heat.py                   热力配色（界面与预览共用）
│   │   ├── mockup.py                 界面静态样张
│   │   └── theme.py                  配色常量
│   ├── scripts\
│   │   ├── start.bat                 pythonw 启动
│   │   └── capture-window.ps1        抓真实窗口截图
│   └── previews\                     生成物：--demo 输出（已 gitignore）
│
└── tools\                            构建/签名/整理，不进包、不被 import
    ├── README.md
    ├── make-exe.bat                  PyInstaller onedir 打包 + 自签名
    ├── exe-version.py                版本资源（会被 PyInstaller eval）
    ├── app.ico                       应用图标
    ├── codesign.pfx                  ⚠ 签名私钥，已 gitignore
    ├── clean-artifacts.bat           清理运行期垃圾（删前要输 yes）
    └── DIRECTORY.md                  本清单
```

## 本次整理做了什么

| 动作 | 对象 | 状态 |
| --- | --- | --- |
| 复制并改路径 | `keylens\make-exe.bat` → `tools\make-exe.bat` | ✅ 新文件已就位，内部引用改为 `tools\app.ico` / `tools\exe-version.py` |
| 复制 | `keylens\exe-version.py` → `tools\exe-version.py` | ✅ 新文件已就位，内容未改 |
| 新建 | `.gitignore` | ✅ 覆盖生成物 + 签名私钥 |
| 新建 | `tools\README.md`、`tools\DIRECTORY.md`、`tools\clean-artifacts.bat`、`tools\tools-move.bat`（本脚本） | ✅ |
| 已完成 | `keylens\app.ico`、`keylens\codesign.pfx` → `tools\` | ✅ `app.ico` 已在 `tools\`；`codesign.pfx` 没找到（签名会被 make-exe.bat 跳过） |
| 已完成 | 删除 `keylens\make-exe.bat`、`keylens\exe-version.py` | ✅ 两个旧占位文件已不在 |
| 已完成 | 删除 `keylens\scripts\start.vbs.bat` | ✅ 已删（连同 keylens-previews\、debug.log、`__pycache__`） |
| 已完成 | 删除 `tools\move-assets.bat`、`tools\tools-move.bat` | ✅ 一次性搬运脚本，使命完成后删掉 |

**一个 import 都没动，包内源码结构保持不变。**

> 为什么 `run.py` 留在包根：`tools\make-exe.bat` 用的是 `--paths "%CD%"` + `keylens\run.py`，
> PyInstaller 靠这个把 `keylens` 当包解析。把 `run.py` 挪进 `tools\` 就得同步改打包参数，
> 收益很小，所以不动。

## 待办

已完成（2026-10-23 清理，共释放约 239 MB）：

1. ✅ 删掉 `keylens\debug.log`（搜狗输入法的 `SGRenderLog` 日志）。它会再次生成——
   第三方程序往"当前工作目录"写，不是代码 bug，定期跑 `clean-artifacts.bat` 即可。
2. ✅ 删掉 `keylens\scripts\start.vbs.bat`、`keylens\keylens-previews\`。
3. ✅ 删掉 `keylens\**\__pycache__\`（里面是 `cpython-314` 的 pyc）。
4. ✅ 删掉 `build\`、`dist\KeyLens-dir.old` / `.old2` / `.old3`，以及最终整理时清空的整个 `dist\`
   （`make-exe.bat` 会重新生成；打包好的 exe 安装在 `%LOCALAPPDATA%\Programs\KeyLens`）、
   `*.spec`、`tools\move-assets.bat`、`tools\tools-move.bat`、`tools\_snap_crop.png`、
   `tools\keylens.db`——都是生成物或一次性脚本。
5. ✅ 根目录 `README.md` 已补上（工作目录总览），不再是缺失项。

仍未处理：

1. 包内的三个 `__init__.py` 仍是 0 字节，可以各加一行 docstring 说明职责。
2. 根目录的 5 个 `*.ps1` 与 KeyLens 无关（zip 加密 / Bandizip 密码 / DITTO 取证排查），
   属于另一个任务留下的记录，本次未动。
3. `_diag\` 里的 `run-diag*`、`base_*.txt`、`tasks.csv` 等是早前排查环境的产物，也不属于 KeyLens。

## 建议的启动方式（避免再往包根写日志）

```bat
@echo off
cd /d "%LOCALAPPDATA%"
start "KeyLens" "D:\RJ\Python\3.14.3\pythonw.exe" -m keylens.main
```

前提是包目录在 `PYTHONPATH` 里；否则保持现在的 `cd` 到项目根，并把 `debug.log` 交给
`clean-artifacts.bat` 定期清。
