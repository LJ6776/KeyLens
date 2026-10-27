# KeyLens · 本机自用按键记录器

一个**看得见、关得掉、只存本地**的按键记录与统计工具。用来回答这类问题：

- 我今天一共敲了多少键？打字最快的是哪个时段？
- 哪个键最费手（热力图），有没有必要换键盘 / 改快捷键？
- 同事用远程控制软件（向日葵 / ToDesk / TeamViewer …）帮我操作的那段时间，本机被输入了多少、分别落在哪些软件里？
- 各应用分别"吃掉"了我多少按键？
- 我今天复制粘贴了多少次，粘进来的大概是文本、图片还是文件？

## 它是什么，不是什么

| 是 | 不是 |
| --- | --- |
| 常驻**可见窗口**的监控工具，标题栏直接写着"按键记录器" | 后台隐藏运行的键盘记录器 |
| 只统计"哪个键、多少次、什么时候、前台是哪个程序" | 不生成任何字符内容，也没有还原输入文本的功能 |
| 复制 / 粘贴只记"次数、内容类型、字符数、哪个应用" | 默认不保存剪贴板正文（`store_text` 默认 `false`） |
| 按键浮层只显示键位名，1.6 秒后消失 | 不落库、无历史、不能回放；密码框与掩码窗口下自动隐藏 |
| 数据只写进本地 `%LOCALAPPDATA%\KeyLens\keylens.db` | 代码里**没有任何网络调用**，不上传、不同步 |
| 窗口上有暂停 / 退出按钮，一键可停 | 没有开机自启、没有隐藏进程、没有反卸载 |
| 密码管理器 / 浏览器等命中隐私掩码，只计数不记键位 | 不记录密码框里敲了什么 |

**使用边界**：这个工具只应该用在你自己的、你拥有且有权监控的设备上。在别人的设备上、
或未告知同机使用者的情况下采集输入，在中国大陆可能触及《刑法》第 285 条，
在《个人信息保护法》下也缺少合法性基础；其他司法辖区（GDPR、CFAA 等）同样严格。
远程会话场景请确保对方知情——你记录的是"这段时间本机被输入了多少"，不是偷偷录对方的密码。

剪贴板同理：默认配置下它只回答"复制粘贴了几次、大概多大"，**不保存你复制了什么**。
如果你把 `clipboard.store_text` 打开，剪贴板里的口令、令牌、身份证号都会以明文落库，
请把这一步当成明确的风险选择，而不是默认行为。

## 运行要求

- Windows 10 / 11（依赖 `WH_KEYBOARD_LL` 输入钩子）
- Python 3.9+，需要带 tkinter 的标准发行版（[python.org](https://www.python.org/downloads/windows/) 官方安装包自带）
- **零第三方依赖**：Win32 调用全部通过 `ctypes` 直接完成，不需要 `pywin32` / `pynput` / `pystray`
- Pillow 可选：有则给窗口画一个彩色图标，没有也不影响任何功能

## 快速开始

先跑一次自检（不装钩子、不开窗口，纯逻辑 + 报告链路校验）：

```powershell
cd <项目根目录>            # 仓库根，含 keylens\ 与 tools\ 两个子目录
python -m keylens.main --self-test
```

全部通过会打印 `自检全部通过。` 并返回 0；有失败项会逐条列出并返回 1。

然后正常启动：

```powershell
python -m keylens.main
```

也可以直接跑脚本 `python keylens\main.py`（命令行参数完全一样，程序会自动补上包路径），
或者双击根目录的 `启动KeyLens.pyw`：`.pyw` 关联到 `pythonw`，双击直接启动，全程没有控制台，
也不会弹「打开文件 - 安全警告」。

为什么不用 `.bat` / `.vbs` / 快捷方式：这三种类型都在 Windows 的「潜在不安全文件类型」清单里，
双击时外壳会先弹一次「打开文件 - 安全警告」；`.bat` 随后还会带出 `cmd.exe` 控制台窗口。
`.pyw` 不在该清单内，所以二者都没有。（`scripts\start.bat` 仍可用，只是会经历上面两下弹窗。）

### 打包为 exe

```bat
tools\make-exe.bat
```

（前置：`pip install pyinstaller pillow`。）两种打包方式：

| 脚本 | 产物 | 说明 |
| --- | --- | --- |
| `tools\make-exe.bat` | `dist\KeyLens\KeyLens.exe`（文件夹，onedir） | 启动快；自签名后自动装到 `%LOCALAPPDATA%\Programs\KeyLens`，日常用这个 |
| `tools\make-exe-onefile.bat` | `dist\KeyLens.exe`（**单文件**，onefile） | 一个文件方便拷走；启动时要解压到临时目录，慢 1–3 秒，个别杀软会拦解压 |
| `tools\make-setup.bat` | `D:\HC\KeyLens\release\KeyLens-1.0.0-setup.exe`（**安装包**） | 向导式：许可协议 → 安装目录 → 快捷方式 → 进度 → 完成；支持 `/S` 静默、`/D=目录`；自带 `uninstall.exe` 并注册卸载入口。用系统自带 `csc.exe` 编译 `tools\installer\*.cs`，不需要 Inno 或 NSIS |

发布时三个包放在 `release\`：`KeyLens-1.0.0-setup.exe`（安装包）、
`KeyLens-1.0.0-portable.zip`（便携包）、`KeyLens-1.0.0-single.exe`（单文件），
外加 `README.txt` 与 `SHA256SUMS.txt`。

**不要直接双击 `dist` 里的 exe**：部分安全软件会拦截"从工程目录启动"的
打包 exe 写数据库（表现为 `WinError 5` / "无法打开数据库"）；复制到别处（安装目录、
用户数据目录等）就完全正常。

exe 与 `python main.py` 功能完全一致（含托盘 / 热键 / 浮层 / 报告）；注意 `--windowed`
打包没有控制台，`--report` / `--self-test` 等命令行子命令的 stdout 不会显示，日常用 GUI 即可。
另外本机外壳对 `.exe` 也会先弹一次「打开文件 - 安全警告」，点"打开"即进入程序。

打包资产（图标、版本资源、签名私钥、打包脚本）都在 `tools\`，不再混在包里。

窗口打开后即开始记录，标题栏显示 `KeyLens 按键记录器 · 正在记录 · 今日 N 次`。

### 托盘与快捷键

- **系统托盘**：启动后在托盘区有 KeyLens 图标——**左键单击**切换主界面显示 / 隐藏，
  **右键**弹出菜单（显示 / 隐藏、退出）。
- **全局热键 `Ctrl+Alt+K`**：任何地方按下都能在"托盘中隐藏"和"显示主界面"之间切换。
- 关闭按钮（或 `Esc`）仍是**真正退出**：停止记录并移除托盘图标；想暂时收起请用托盘或热键。

### 更改 / 迁移数据目录

界面上的 **「更改数据目录」** 按钮可以把数据整体搬家：选择新位置后，程序会
短暂停止记录 → 把 `keylens.db`、`reports\`、溢出文件等搬到新目录 → 更新配置 → 自动恢复记录。
配置会同时写到默认目录与新目录，保证下次启动（含 exe）直接生效。

## 先看效果（不记录任何真实按键）

```powershell
python -m keylens.main --demo
```

会在当前目录生成 `keylens-previews\`，里面两份可直接双击的 HTML：

| 文件 | 内容 |
| --- | --- |
| `sample-report.html` | **示例报告**，由 `report.write_daily_report()` 这个真实函数渲染，含热力图、分时分布、键位排行、类别占比、应用分布、复制/粘贴小节、本地/远程会话对照表、**按键时间线（按时间顺序列出非掩码按键，密码等掩码键不出现）** |
| `window-mockup.html` | **界面样张**，复刻真实窗口的布局与配色（指标卡、热力图、掩码提示条） |

示例数据只写进临时数据库，生成完立即删除，**不碰你的 `keylens.db`**；键位布局与热力配色直接复用
`core/keymap.py` 与 `ui/heat.py`，所以预览和实际输出不会走样。

想拿**真实窗口截图**（会包含你自己的统计数字）：

```powershell
powershell -ExecutionPolicy Bypass -File keylens\scripts\capture-window.ps1
```

只做屏幕位图拷贝，不注入进程、不读内存；截图内容属于你自己的数据，别随意外发。

## 界面说明

- **顶部指标卡**：今日按键 / 本会话按键 / 最近 1 分钟 / 掩码按键 / 复制 · 粘贴 / 本会话时长
- **键盘热力图**：按真实键位布局着色，颜色越暖说明这个键按得越多，格子里直接显示次数
- **最常按的键**、**按键分布（按前台应用）**
- **状态行**：当前是暂停还是记录、会话编号、当前是本地输入还是远程会话、掩码是否开启、
  剪贴板监听是否在运行（以及是否开启了存正文）、有没有事件丢弃
- **橙色提示条**：当前前台应用命中隐私掩码时出现
- **按键时间线**：界面底部实时按时间顺序滚动显示当天按键（时间 / 键位 / 前台应用）；
  右上角下拉框可**按前台程序筛选**，只看某个程序里敲了什么；
  掩码场景显示「隐私掩码 · 不记录内容」，不显示具体键位
- **剪贴板记录**：界面底部实时显示当天复制 / 粘贴记录（时间 / 类型 / 来源程序 / 内容）；
  右上角下拉框可按前台程序筛选；粘贴会沿用最近一次复制的内容与正文；
  复制**图片**时自动把图片存成 ``%TEMP%\\KeyLens-clips\\clip-*.bmp``（保留 3 天），
  面板里显示文件链接，**双击该行**即可打开预览并把图片重新放回剪贴板（可用 ``clipboard.save_images`` 关闭）；
  要显示复制的正文，需把 ``config.json`` 里的 ``clipboard.store_text`` 设为 ``true``
  （正文只保留前 ``max_text_chars`` 个字符）；掩码场景显示「隐私掩码 · 不记录内容」
- **按键浮层**按钮：打开 / 关闭屏幕右下角的实时按键浮层（详见「按键浮层」一节）
- **清除记录**按钮：删除本机全部按键与剪贴板记录，点击后二次确认，不可恢复
- **生成报告**按钮：生成当天 HTML 报告并打开（详见「报告」一节）
- **打开数据目录**按钮：打开存放 `keylens.db` 的本地目录
- **退出**按钮：停止记录并关闭窗口

## 命令行参数

```
python -m keylens.main                 # 打开窗口并记录
python -m keylens.main --paused        # 先暂停，手动点"恢复记录"再开始
python -m keylens.main --data-dir D:\kl # 指定数据目录
python -m keylens.main --self-test     # 自检：不装钩子、不开窗口，验证统计与报告链路
python -m keylens.main --osd           # 启动时打开按键浮层（实时显示按键，不落库）
python -m keylens.main --demo          # 生成示例报告 + 界面样张到 .\keylens-previews
python -m keylens.main --demo D:\p --demo-total 30000   # 换目录、换示例数据量
python -m keylens.main --report        # 只生成今天的 HTML 报告，不开界面
python -m keylens.main --report 2024-05-01
python -m keylens.main --range 7       # 最近 7 天汇总报告
python -m keylens.main --purge-days 30 # 删除 30 天前的记录
python -m keylens.main --wipe          # 清空全部记录（需输入 yes 确认）
```

报告文件写在 `<数据目录>\reports\`，是单文件 HTML，双击用浏览器打开即可，
里面包含热力图、分时分布、键位排行、类别占比、应用分布、**复制 / 粘贴概况**
和**本地 / 远程会话对照表**。

## 远程输入（向日葵 / ToDesk / TeamViewer / VNC / RustDesk …）是怎么记录的

关键点：远程控制软件是把键盘事件**注入到本机输入队列**的，因此：

- 本工具的低级钩子**同样会收到**这些事件，无需任何额外适配；
- 事件里带 `LLKHF_INJECTED` 标志，数据库 `flags` 字段会记下 16，可用于区分注入来源；
- 程序每 5 秒探测一次远程软件（窗口标题关键字 + 前台进程名），
  远程会话**开始时自动切一个新会话**并标记 `remote=1`，结束时再切回本地会话；
- 报告里的"会话明细"表会按 `远程会话 / 本地输入` 分开列出，并给出各自的按键数。

**被识别的远控 / 监控软件**（进程名，见 `core/winapi.py` 的 `REMOTE_PROCESS_NAMES`）：
向日葵 / ToDesk / TeamViewer / AnyDesk / RustDesk / 远程桌面(RDP) / Chrome 远程桌面 /
VNC 全系（vncviewer、winvnc、tightvnc、realvnc…）/ Parsec / NoMachine / Splashtop /
Radmin / ScreenConnect / DwService / LogMeIn / Zoho Assist / Jump Desktop 等。
想加自家环境里的其他远控软件，往那个集合里添进程名（小写、不带扩展名）即可。

**这些软件被识别后会有三处体现**：

1. 状态栏显示 `远程/监控软件输入中（向日葵）` 这样的标注（而不是笼统的"远程会话中"）；
2. 按键浮层底部提示行会变成 `远程 / 监控软件输入中 · 向日葵`（按键照常显示，不隐藏）；
3. 报告新增 **「远程控制 / 监控软件输入」** 小节，按软件列出各自的按键数——
   也就是"在远控软件里一共敲了多少、各软件分别多少"。

也就是说，你能回答"这段时间的输入是本地键盘还是远程注入、各有多少、分别落在哪些软件里"，
但不会因此得到任何具体输入内容。

远程会话里按下的 `Ctrl+V` 也会被识别为粘贴，并在 `injected` 字段标 1，
报告里单独列成"其中远程注入 N 次"。

## 复制 / 粘贴是怎么记录的

键盘钩子只能看到 `Ctrl+C` / `Ctrl+V` 这两个键，看不出复制粘贴的是一段文字、一张图
还是一批文件，也看不出规模。所以复制走剪贴板监听，粘贴走组合键判定：

| 动作 | 怎么发现 | 记什么 |
| --- | --- | --- |
| 复制 / 剪切 / 程序写入 | `AddClipboardFormatListener` 订阅 `WM_CLIPBOARDUPDATE` | 内容类型、字符数、字节数、前台应用 |
| 粘贴 `Ctrl+V` / `Ctrl+Shift+V` / `Shift+Insert` | 键盘钩子 + `GetAsyncKeyState` 判修饰键 | 次数、沿用最近一次剪贴板快照的类型与字符数、是否远程注入 |

几个实现上的细节：

- 一次复制会连着发好几条更新（先清空、再写入，中间还有 `DataObject` 之类的过渡格式）。
  程序等剪贴板静默 120 毫秒后再读，**保证一次复制只记一条**，且读到的是最终内容。
- 粘贴时不重新打开剪贴板（钩子回调里不能做可能阻塞的 IO），而是沿用 60 秒内的
  最近一次快照；刚启动就粘贴、还没有快照时会记成"未知（没取到剪贴板快照）"。
- 命中隐私掩码时，复制粘贴一律只记"发生过一次"，类型、长度、正文都不取。
- 全部记在 `clipboard` 表里，和按键事件一样受保留期清理约束。

想关掉：`config.json` 里 `clipboard.enabled = false`；只想记粘贴不想记剪贴板内容变化：
`clipboard.track_copy = false`（这时连监听线程都不起）。

## 按键浮层（实时显示，不落库）

界面上的**按键浮层**按钮（或 `python -m keylens.main --osd`）会开一个小浮层，
把刚按下的键以短动画的形式显示在屏幕右下角：可拖动、始终置顶、200 毫秒刷新一次、
单个按键停留 1.6 秒后淡出。

它和"记录"是两套完全分开的东西：

| | 记录（落库） | 按键浮层 |
| --- | --- | --- |
| 存在哪 | SQLite 数据库 | 只在内存里，关掉就没了 |
| 存多久 | 到保留期（默认 90 天） | 1.6 秒 |
| 能回看吗 | 只能看统计与报告 | 不能，没有历史 |

浮层显示的是**键位名**（`Ctrl + S`、`Enter`、`空格`），和热力图用的是同一套键位标识，
不是"输入了什么字符"，也不做字符还原。

**自动隐藏**：命中隐私掩码（含 WinRAR / 好压 / 7-Zip 这类解压软件）时，或者焦点落在
密码输入框（原生 `ES_PASSWORD` 控件）时，浮层立刻清空并显示原因，焦点离开后自动恢复。

**技术兜不住的两点**：

1. 浏览器、Electron、Qt 里的密码框是自绘的，系统查不到 `ES_PASSWORD` 样式。
   这类场景靠进程掩码兜底（浏览器默认整体掩码，所以浮层在浏览器里不显示）。
   但如果你把 `chrome.exe` 写进了 `allow_unmask`，网页密码框里按的键**会**显示在浮层上——
   这不是 bug，就是 `allow_unmask` 的字面含义。
2. 浮层开着的时候输入口令，口令依然会显示在屏幕上。
   这一步没有技术手段能挡住，只能靠你自己：输口令前把它关掉。

## 隐私掩码

`config.json` 里的 `privacy` 段控制掩码。默认对以下场景只记"按了键"，键位一律归入 `MASKED` 桶：

- **凭据类进程**（`password_apps`）：`1password.exe`、`bitwarden.exe`、`keepassxc.exe`、
  `chrome.exe`、`msedge.exe`、`firefox.exe` 等
- **窗口标题关键字**（`title_keywords`）：`password` / `密码` / `口令` /
  `credential` / `钱包` / `支付` / `登录 - ` 等

浏览器被默认整个掩码，是因为 Windows 无法从窗口标题区分"地址栏打字"和"网页里打字"。
如果你只想统计浏览器里的按键量、不在意失去这些键位的热力图数据，这个默认值正好合适。

**解压 / 加密容器默认不掩码**：WinRAR、好压、7-Zip、Bandizip、VeraCrypt 这些软件
（以及标题里的 `解压` / `解密` / `archive` 等关键字）默认**不在**掩码名单里，
所以在里面输入的口令会照常显示在时间线上。
想要更严的保护，把 `privacy.strict_archive_mask` 设为 `true`：

```json
"privacy": {
  "strict_archive_mask": true
}
```

开启后会连"输入解压密码"这类标题一起拦，并自动把内置名单补回来
（对本机已有的旧 `config.json` 同样生效）。

三个掩码名单的处理是**并集**：内置默认项 ∪ 你在 `config.json` 里写的项。
这样以后新增的默认掩码项会自动生效，不会因为你已经有一份旧配置而悄悄漏掉。
确实要放开某个内置项，把它写进 `allow_unmask`：

```json
"privacy": {
  "allow_unmask": ["chrome.exe"]
}
```

代价自己权衡：放开浏览器后，浏览器里的输入（含网页密码框）不再受掩码保护。

**剪贴板用更宽松的判定**：按键必须拦浏览器，但**复制动作只在下面两种情况被拦**——
窗口标题出现密码 / 口令 / 解压等关键字，或前台是密码管理器、解压 / 加密容器。
也就是说在浏览器里正常复制一段网页文字，报告会照常显示内容；
想恢复"浏览器前台一律不记剪贴板内容"的旧行为，把 `privacy.clipboard_mask_strict` 设为 `true`。

压缩 / 加密容器（WinRAR、好压、7-Zip、VeraCrypt…）在剪贴板这条路上同样默认放行：
在这些软件里复制的文字会照常记录并显示出来，连"输入解压密码"这类标题也不再拦；
`privacy.strict_archive_mask = true` 可以把它们重新拦下。

掩码对剪贴板同样生效：命中掩码时，复制 / 粘贴只记"发生过一次"，
内容类型、字符数、正文一律不取（正文无论配置如何都不存）。

`store_window_titles` 设为 `false` 时，窗口标题一律不入库（只留进程名）。

## 数据与保留

- 默认目录：`%LOCALAPPDATA%\KeyLens\`，配置文件 `config.json`，数据库 `keylens.db`
- 数据库表：`events`（每次按键一行）、`clipboard`（每次复制 / 粘贴一行）、`sessions`（会话）、`daily`、`meta`
- 保留期默认 90 天，`retention_days` 可改；设为 0 表示不自动清理
- 空闲超过 `pause_when_idle_seconds`（默认 300 秒）会自动暂停计入"活跃跨度"；
  会话间隙超过 `session_gap_seconds` 会切成新会话，避免把隔夜的两段工作算成一段
- 界面退出（或 `--wipe`）后建议执行一次 WAL checkpoint，数据库会收缩

`config.json` 默认长这样：

```json
{
  "data_dir": "C:\\Users\\你\\AppData\\Local\\KeyLens",
  "pause_when_idle_seconds": 300.0,
  "session_gap_seconds": 300.0,
  "batch_size": 200,
  "batch_interval": 2.0,
  "remote_poll_seconds": 5.0,
  "retention_days": 90,
  "store_window_titles": true,
  "start_paused": false,
  "clipboard": {
    "enabled": true,
    "track_copy": true,
    "track_paste": true,
    "store_text": false,
    "max_text_chars": 80
  },
  "privacy": {
    "enabled": true,
    "password_apps": ["1password.exe", "bitwarden.exe", "keepassxc.exe", "chrome.exe", "..."],
    "mask_processes": ["winrar.exe", "haozip.exe", "7zfm.exe", "bandizip.exe", "..."],
    "title_keywords": ["password", "密码", "口令", "解压", "解密", "credential", "..."],
    "allow_unmask": [],
    "clipboard_mask_strict": false,
    "strict_archive_mask": false
  }
}
```

## 代码结构

完整的逐文件清单（含构建资产与已清理项）见 `tools\DIRECTORY.md`；这里只列包内结构。

```
keylens/                      纯 Python 包：只放会被 import 的东西
  main.py            入口与命令行（自带 runpy 桥接，也可直接 python main.py）
  run.py             PyInstaller 打包专用入口（必须留在包根：--paths 依赖它）
  report.py          HTML 报告（热力图 / 分时 / 会话对照 / 按键时间线）
  report_module.py   报告模块的兼容转发层
  selftest.py        自检：键位解析 / 存储 / 报告 / 掩码 / 预览，不装钩子不开窗口
  preview.py         示例报告与界面样张生成（--demo）
  make-preview.py    把预览写到 keylens\previews\
  core/              引擎层
    keymap.py        扫描码 -> 键位标识、分类、键盘布局（不含任何字符还原）
    winapi.py        WH_KEYBOARD_LL 钩子、前台窗口、修饰键状态、空闲检测、远程会话探测
    clipboard.py     剪贴板监听（WM_CLIPBOARDUPDATE）、内容类型与规模解析、粘贴组合键判定
    db.py            SQLite 建库、批量写入、统计查询、保留期清理
    tracker.py       采集调度、隐私掩码、会话管理、复制/粘贴落库、实时计数
    tray.py          托盘图标 + 全局热键（纯 ctypes）
  ui/                 表现层
    app.py           常驻可见窗口 + 热力图 + 设置
    osd.py           按键浮层：实时显示按键，只走内存，不落库
    heat.py          热力配色（界面与预览共用，保证两者一致）
    mockup.py        界面静态样张渲染
    theme.py         配色
  scripts/
    start.bat              pythonw 启动脚本
    capture-window.ps1     抓取真实窗口截图

tools/                        构建 / 签名 / 整理：不进包、不被 import
  make-exe.bat        PyInstaller onedir 打包 + 自签名
  exe-version.py      版本资源（会被 PyInstaller eval）
  app.ico             应用图标
  codesign.pfx        ⚠ 代码签名私钥，已加入 .gitignore
  clean-artifacts.bat 清理运行期垃圾（删前要输 yes）
```

线程模型：钩子线程只解析入队 → 剪贴板线程只量类型与长度 → 写入线程批量落库 →
维护线程负责空闲/远程/清理 → 界面线程只读内存计数，全链路没有任何阻塞钩子回调的操作。

## 常见问题

**钩子安装失败（提示以管理员身份运行）**
`SetWindowsHookExW` 返回 0。少数机器受安全软件（尤其带"隐私保护"的国产安全套件）
或组策略限制；先暂时退出安全软件再试。本工具不需要管理员权限，正常情况下普通用户即可运行。

**界面显示"剪贴板监听失败"？**
`AddClipboardFormatListener` 没装上（系统太老或被安全软件拦截）。
键盘记录完全不受影响，状态行会写明具体原因；确认不需要就把 `clipboard.enabled` 设成 `false`。
另外，某些程序写剪贴板的瞬间我们可能读不到（剪贴板被占用），这种情况会跳过本次、不重试，
所以偶发少记一次是正常的，不会影响后续。

**按键统计有延迟？**
界面里的"今日按键"是内存实时计数，"键位排行 / 应用分布 / 会话表"每 15 秒从数据库刷新一次。

**安全软件报毒？**
全局键盘钩子是杀软的重点关注行为，任何同类工具都会被提示。
代码全部可读（纯标准库 + ctypes），数据不出本机，可以把 `winapi.py` 拿去自己审。

**能不能开机自启 / 最小化到托盘？**
故意没做。托盘会需要 `pystray` 之类的依赖，而且"隐藏到托盘 + 开机自启"正是键盘记录器的典型形态，
与这个工具的定位冲突。要常驻就让它开着窗口，一眼看得见。
