# KeyLens

**看得见、关得掉、只存本地**的按键 / 剪贴板统计工具（Windows 10 / 11）。

用来回答这些日常问题：

- 我今天一共敲了多少键？打字最快的是哪个时段？
- 哪个键最费手（热力图），要不要换键盘 / 改快捷键？
- 各应用分别"吃掉"了我多少按键？
- 今天复制粘贴了多少次，粘进来的是文本、图片还是文件？
- 远程控制（向日葵 / ToDesk / TeamViewer…）帮我操作的那段时间，本机被输入了多少？

> 详细文档（配置项、隐私掩码、命令行参数、报告字段、常见问题）见 **[keylens/README.md](keylens/README.md)**。

## 它是什么，不是什么

| 是 | 不是 |
| --- | --- |
| 常驻**可见窗口**的工具，标题栏直接写着"按键记录器" | 后台隐藏运行的键盘记录器 |
| 只统计"哪个键、多少次、什么时候、前台是哪个程序" | 不生成任何字符内容，没有还原输入文本的功能 |
| 复制 / 粘贴只记"次数、内容类型、字符数、哪个应用" | 默认**不保存剪贴板正文**（`clipboard.store_text` 默认 `false`） |
| 按键浮层只显示键位名，1.6 秒后消失 | 不落库、无历史、不能回放；密码框与掩码窗口下自动隐藏 |
| 数据只写进本地 `%LOCALAPPDATA%\KeyLens\keylens.db` | 代码里**没有任何网络调用**，不上传、不同步 |
| 窗口上有暂停 / 退出按钮，一键可停 | 没有开机自启、没有隐藏进程、没有反卸载 |
| 密码管理器 / 浏览器等命中隐私掩码，只计数不记键位 | 不记录密码框里敲了什么 |

## 使用边界（请先读这一段）

这个工具只应该用在你自己的、你拥有且有权监控的设备上。在别人的设备上、或未告知同机
使用者的情况下采集输入，在中国大陆可能触及《刑法》第 285 条，在《个人信息保护法》下也
缺少合法性基础；其他司法辖区（GDPR、CFAA 等）同样严格。远程会话场景请确保对方知情。

剪贴板同理：默认配置下它只回答"复制粘贴了几次、大概多大"，**不保存你复制了什么**。
把 `clipboard.store_text` 打开后，剪贴板里的口令、令牌、身份证号都会明文落库——请把
这一步当成明确的风险选择，而不是默认行为。

## 运行要求

- Windows 10 / 11（依赖 `WH_KEYBOARD_LL` 输入钩子）
- Python 3.9+，需要带 tkinter 的标准发行版（[python.org](https://www.python.org/downloads/windows/) 官方安装包自带）
- **零第三方依赖**：所有 Win32 调用通过 `ctypes` 直接完成，不需要 `pywin32` / `pynput` / `pystray`
- Pillow 可选：有则给窗口画彩色图标，没有也不影响任何功能

## 快速开始

```powershell
# 1) 自检：不装钩子、不开窗口，纯逻辑 + 报告链路校验
python -m keylens.main --self-test

# 2) 正常启动（托盘 + 全局热键 Ctrl+Alt+K）
python -m keylens.main

# 3) 看效果，不记录任何真实按键（生成示例报告与界面样张）
python -m keylens.main --demo
```

也可以双击根目录的 `启动KeyLens.pyw`：`.pyw` 关联到 `pythonw`，双击直接启动，全程没有控制台。

常用命令行参数：

```
python -m keylens.main                 # 打开窗口并记录
python -m keylens.main --paused        # 先暂停，手动点"恢复记录"再开始
python -m keylens.main --data-dir D:\kl # 指定数据目录
python -m keylens.main --self-test     # 自检
python -m keylens.main --osd           # 启动时打开按键浮层（实时显示，不落库）
python -m keylens.main --demo          # 生成示例报告 + 界面样张
python -m keylens.main --report        # 只生成今天的 HTML 报告
python -m keylens.main --range 7       # 最近 7 天汇总报告
python -m keylens.main --purge-days 30 # 删除 30 天前的记录
python -m keylens.main --wipe          # 清空全部记录
```

## 打包成 Windows 程序

前置：`pip install pyinstaller pillow`

| 脚本 | 产物 | 说明 |
| --- | --- | --- |
| `tools\make-exe.bat` | `dist\KeyLens\KeyLens.exe`（文件夹，onedir） | 启动快；自签名后自动装到 `%LOCALAPPDATA%\Programs\KeyLens` |
| `tools\make-exe-onefile.bat` | `dist\KeyLens.exe`（**单文件**，onefile） | 一个文件方便拷走；启动时解压到临时目录，慢 1–3 秒 |
| `tools\make-setup.bat` | `release\KeyLens-1.0.0-setup.exe`（**安装包**） | 向导式：许可协议 → 安装目录 → 快捷方式 → 进度 → 完成；支持 `/S` 静默、`/D=目录`；自带 `uninstall.exe` 并注册卸载入口 |

发布时三个包放在 `release\`：`KeyLens-1.0.0-setup.exe`（安装包）、
`KeyLens-1.0.0-portable.zip`（便携包）、`KeyLens-1.0.0-single.exe`（单文件），
外加 `README.txt` 与 `SHA256SUMS.txt`。

> **安全软件误报**：全局键盘钩子是杀软的重点关注行为，任何同类工具都会被提示。
> 代码全部可读（纯标准库 + `ctypes`），数据不出本机，可以把 `core/winapi.py` 拿去自己审。
> 另外部分安全软件会拦截"从工程目录启动"的打包 exe 写数据库（`WinError 5`），
> 把 exe 复制到安装目录 / 用户目录运行即可。

## 项目结构

```
keylens/                      纯 Python 包：只放会被 import 的东西
  main.py            入口与命令行
  run.py             PyInstaller 打包专用入口
  report.py          HTML 报告（热力图 / 分时 / 会话对照 / 按键时间线）
  selftest.py        自检：键位解析 / 存储 / 报告 / 掩码 / 预览
  preview.py         示例报告与界面样张生成（--demo）
  core/              引擎层（keymap / winapi / clipboard / db / tracker / tray）
  ui/                表现层（app / osd / heat / mockup / theme）
  scripts/           启动脚本、窗口截图脚本
tools/                        构建 / 打包 / 签名 / 整理脚本，不进 Python 包
  make-exe.bat / make-exe-onefile.bat / make-setup.bat
  installer/         安装向导与卸载程序的 C# 源码（用系统自带 csc.exe 编译）
  exe-version.py     Windows 版本信息资源（会被 PyInstaller eval）
  app.ico            应用图标
启动KeyLens.pyw / .bat         双击启动入口
```

逐文件清单见 [tools/DIRECTORY.md](tools/DIRECTORY.md)，
打包手册见 [tools/BUILD.md](tools/BUILD.md)。

## 数据与保留

- 默认目录 `%LOCALAPPDATA%\KeyLens\`：`config.json`（配置）+ `keylens.db`（SQLite）
- 表：`events`（每次按键）、`clipboard`（每次复制 / 粘贴）、`sessions`、`daily`、`meta`
- 保留期默认 90 天（`retention_days`），设为 0 表示不自动清理
- 空闲超过 300 秒自动暂停计入"活跃跨度"；会话间隙超过 300 秒切成新会话
- 界面里有「更改数据目录」按钮，可以把数据整体搬到别处

## 常见问题

**钩子安装失败（提示以管理员身份运行）**
`SetWindowsHookExW` 返回 0。少数机器受安全软件或组策略限制；本工具不需要管理员权限，
正常情况下普通用户即可运行。

**安全软件报毒？**
见上文"安全软件误报"。全局键盘钩子是杀软重点关注行为，数据不出本机。

**能不能开机自启 / 最小化到托盘？**
故意没做。托盘会需要额外依赖，而且"隐藏到托盘 + 开机自启"正是键盘记录器的典型形态，
与这个工具的定位冲突。要常驻就让它开着窗口，一眼看得见。

## License

见 [LICENSE](LICENSE)。
