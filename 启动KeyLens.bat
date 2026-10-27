@echo off
rem 用系统已信任的 Python 直接启动 KeyLens（绕开杀软对打包 exe 的拦截）
rem 效果与 exe 完全一致：托盘、Ctrl+Alt+K、数据都在 %LOCALAPPDATA%\KeyLens
rem 注意：.bat 在 Windows「潜在不安全文件类型」清单里，双击时外壳会先弹「打开文件 - 安全警告」，
rem 确认后还会出现 cmd/终端窗口。要双击即启动、全程无弹窗，请用同目录的 启动KeyLens.pyw。
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "KeyLens" pythonw -m keylens.main
) else (
    start "KeyLens" python -m keylens.main
)
