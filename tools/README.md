# tools/ · 构建、打包与整理脚本

这里放**不属于 Python 包**的东西。之所以不放在 `keylens\` 里，是因为 Python 只接受
合法标识符做模块名——`make-exe.bat`、`exe-version.py`、`app.ico`、`codesign.pfx`
既不能作为包内模块被导入，散在包根还会让"哪些是源码、哪些是构建资产"分不清。

| 文件 | 用途 |
| --- | --- |
| `make-exe.bat` | PyInstaller **onedir** 打包（启动快，不必解压），打完用本地证书自签名，并安装到 `%LOCALAPPDATA%\Programs\KeyLens` |
| `make-exe-onefile.bat` | PyInstaller **onefile** 打包：产出单文件 `KeyLens.exe`（复制到 `release\`），方便拷走；启动要解压，慢 1–3 秒 |
| `make-setup.bat` | 生成**安装包** `KeyLens-1.0.0-setup.exe`：向导式（许可协议 → 选安装目录 → 选快捷方式 → 进度 → 完成），支持 `/S` 静默与 `/D=路径`，输出到 `release\` |
| `installer\installer.cs` | 安装向导的 C# 源码（`make-setup.bat` 用系统自带 `csc.exe` 编译，不依赖 Inno/NSIS） |
| `installer\uninstaller.cs` | 卸载程序的 C# 源码，安装时写成 `uninstall.exe`，并注册到「设置 → 应用」的卸载入口 |
| `exe-version.py` | Windows 版本信息资源。**会被 PyInstaller 直接 `eval()`**，必须保持为单个表达式，不能有 import/赋值 |
| `app.ico` | 应用图标（窗口 + exe） |
| `codesign.pfx` | 代码签名**私钥**。已加入 `.gitignore`，不要提交、不要同步、不要外发 |
| `clean-artifacts.bat` | 清理运行期垃圾（日志、`__pycache__`、旧预览、build 产物），删前列清单并要你输入 `yes` |
| `_snap_debug.py` | 开发工具：示例数据启动主窗口（**不装键盘钩子**），1.8 秒后截图到 `tools/_snap.png` 并退出。改完界面跑一下看效果，不必启动正式程序 |
| `DIRECTORY.md` | 逐文件的目录清单与去留表 |

## 打包

```bat
tools\make-exe.bat
```

前置：`pip install pyinstaller pillow`。输出 `dist\KeyLens\KeyLens.exe`，签名后**自动安装到
`%LOCALAPPDATA%\Programs\KeyLens`**。日常从安装目录运行；**不要直接跑 `dist` 里那份**——
本机安全软件会拦截"从工作目录启动"的打包 exe 写数据库（报 `WinError 5` / 无法打开数据库），
复制到用户目录后同一个 exe 就正常了。

## 关于私钥

`codesign.pfx` 是你的真实签名私钥。它现在被 `.gitignore` 排除，但请注意：

- 它躺在磁盘上就是明文风险，口令强度决定实际安全性；
- 别把它拷进任何网盘 / 聊天工具 / 公开仓库；
- 换机器时优先从证书存储导出，而不是拷贝这个文件。
