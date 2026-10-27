# 打包 exe · 运行手册

> **重要前提**：我（助手）在当前环境里**无法执行任何进程**（每次 `pwsh` 都返回
> `0xC0000142`，DLL 初始化失败），所以这份手册是给你在自己终端里执行的。
> 里面的判据全部写成"看到什么算通过、看到什么算失败"。

## 0. 先解决两个阻塞项

### 阻塞项 1：`make-exe.bat` 和 `exe-version.py` 被我的迁移占位覆盖了

上一轮整理目录时，我把 `keylens\make-exe.bat`、`keylens\exe-version.py` 复制到 `tools\`
之后，把旧位置改成了提示用的占位文件。**副作用**：

- 仓库里唯一的打包脚本 `keylens\make-exe.bat` 现在是 6 行占位（CodeBuddy 写的版本已不在）；
- 仓库里唯一的版本资源 `keylens\exe-version.py` 现在是 `raise SystemExit(...)`，而
  PyInstaller 用 `eval()` 读这个文件，**原样打包必然失败**；
- `keylens\app.ico` 还在原位，`tools\app.ico` 还没生成。

正式脚本在 `tools\make-exe.bat`（路径已改指 `tools\app.ico` 与 `tools\exe-version.py`）。

> 注（2026-10-23）：搬家脚本 `move-assets.bat` / `tools-move.bat` 已完成使命并删除，
> 下面这些步骤只作历史记录；现在直接用 `tools\make-exe.bat` 即可。

**做法二选一：**

```bat
rem 方案 A（跟着新目录走）：先搬家，再用 tools\ 下的脚本
tools\move-assets.bat
tools\make-exe.bat
```

```bat
rem 方案 B（回到原地，保持 CodeBuddy 的布局）：把两个文件改回 keylens\
copy /y tools\exe-version.py keylens\exe-version.py
rem make-exe.bat 的 CodeBuddy 原版内容见本仓库历史；若已丢失，
rem 可直接用 tools\make-exe.bat，只把里面的 tools\ 前缀改回 keylens\
```

### 阻塞项 2：`--wipe` 在打包后的 exe 里会崩

`keylens\main.py:140` 用 `input()` 做二次确认。PyInstaller `--windowed` 打包后
**没有控制台，`sys.stdin` 为 `None`**，`input()` 会抛 `RuntimeError: input(): lost sys.stdin`。
GUI 正常用没问题，只有 `KeyLens-dir.exe --wipe` 这条命令行会崩。

建议改法（`main.py` 里加一个守卫）：

```python
def _confirm(prompt: str) -> bool:
    """有控制台就用 input()，打包成 windowed exe 后改用弹窗。"""
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            return input(prompt).strip().lower() == "yes"
        except (EOFError, RuntimeError):
            pass
    try:
        import tkinter.messagebox as mb
        return bool(mb.askyesno("KeyLens", prompt))
    except Exception:
        return False
```

然后把 `--wipe` 分支里的 `input(...)` 换成 `_confirm(...)`。

## 1. 前置环境

```powershell
cd <项目根目录>              # 含 keylens\ 与 tools\ 两个子目录
python -V                      # 应为 3.14.x（pyc 是 cpython-314）
python -m pip install pyinstaller pillow
```

`pillow` 不是必须（没有就退回系统图标），但托盘图标和窗口图标都用它画，建议装。

## 2. 打包前自检（这一步是硬门槛）

```powershell
python -m keylens.main --self-test
```

**通过判据**：末尾打印 `自检全部通过。`，退出码 0。
自检里新增了一节"打包前置检查"，会验证：

- `tools\exe-version.py` 是合法的 `VSVersionInfo(...)` 单表达式（AST 校验）；
- `make-exe.bat` 里引用的 `app.ico` / `exe-version.py` 路径真实存在；
- `--onedir`、`--windowed`、`--paths`、入口 `keylens\run.py` 齐备。

**失败判据**：出现 `FAIL 脚本引用的 tools\app.ico 存在` ⇒ 确认 `tools\app.ico` 还在
（搬家脚本已删除，直接从备份或图标源补回来即可）。

## 3. 打包

```bat
tools\make-exe.bat
```

等价的手工命令（脚本内部就是这条）：

```powershell
python -m PyInstaller --noconfirm --onedir --windowed --name KeyLens `
    --icon tools\app.ico `
    --version-file tools\exe-version.py `
    --paths "$PWD" `
    keylens\run.py
```

**通过判据**：`dist\KeyLens\KeyLens.exe` 存在，且同目录下有 `_internal\`。

> 2026-10-23 起 `make-exe.bat` 在打包、签名后还会把整个目录复制到
> `%LOCALAPPDATA%\Programs\KeyLens`，**验收请跑安装目录那份**：本机安全软件会拦截
> "从工作目录启动"的打包 exe 写数据库（`WinError 5` / 无法打开数据库），
> 同一份 exe 复制到用户目录就正常。
**失败判据**：

| 现象 | 原因 |
| --- | --- |
| `SyntaxError` / `could not evaluate version file` | `--version-file` 指到了占位版 `exe-version.py` |
| `Unable to find "tools\app.ico"` | 图标还没搬进 `tools\` |
| `ModuleNotFoundError: keylens` | 少了 `--paths "%CD%"`，或入口不是 `keylens\run.py` |

## 4. 打包后验收（必须逐条做，静态检查替不了）

1. **能启动、有可见窗口**：双击 `%LOCALAPPDATA%\Programs\KeyLens\KeyLens.exe`，
   窗口标题应显示 `KeyLens 按键记录器 · 正在记录 · 今日 N 次`。
2. **托盘图标出现**，左键单击能切换窗口显示 / 隐藏；`Ctrl+Alt+K` 同样生效。
3. **数据落在正确位置**：随便敲几个键，确认
   `%LOCALAPPDATA%\KeyLens\keylens.db` 的时间戳在变。
4. **浮层**：配置里开启 `osd` 后，按键时应在屏幕角落看到键位名（1.6 秒消失）。
5. **报告**：窗口里点"生成报告"，确认 `%LOCALAPPDATA%\KeyLens\reports\` 下有 HTML。
6. **退出干净**：关窗口后，任务管理器里不应残留 `KeyLens.exe` 进程，
   托盘图标应消失。

> 注意：`--windowed` 没有控制台，所以 `--report` / `--self-test` / `--demo` 这些
> 命令行子命令的 `print` 输出看不到。要跑这些，用源码方式 `python -m keylens.main --xxx`。

## 5. 已知限制

- **我无法在这里运行任何东西**，第 2 步的自检与第 4 步的验收必须你来跑；
  上面的代码审查只覆盖"静态可判定"的问题（未定义名、跨模块引用对不上、
  模板占位符与字典不匹配、`None` 占位解包、runpy 递归、ctypes 64 位截断）。
- 逻辑错误、多线程竞态、Win32 返回值处理不当，静态审查**看不出来**。
- 杀软可能拦截新 exe 的首次写入（`main.py:130` 已经为这种情况准备了友好提示）。
