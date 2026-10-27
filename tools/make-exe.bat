@echo off
rem KeyLens 打包脚本（onedir：避开安全软件对 onefile 临时解压目录的拦截）
rem
rem 前置：pip install pyinstaller pillow
rem 用法：在项目根目录双击本文件，或执行  tools\make-exe.bat
rem
rem 产物：dist\KeyLens\KeyLens.exe，并自动安装到 %LOCALAPPDATA%\Programs\KeyLens
rem 注意：不要直接运行 dist 里那份 exe —— 本机安全软件会拦截"从本工作目录启动"的打包 exe
rem      写数据库（报 WinError 5 / 无法打开数据库）；安装目录里的副本没有这个问题。
setlocal
cd /d "%~dp0.."

set PYTHON=python
where %PYTHON% >nul 2>nul || set PYTHON=D:\RJ\Python\3.14.3\python.exe

%PYTHON% -m PyInstaller --noconfirm --onedir --windowed --name KeyLens ^
    --icon tools\app.ico ^
    --version-file tools\exe-version.py ^
    --add-data "tools\app.ico;." ^
    --paths "%CD%" ^
    keylens\run.py

rem 本地自签名：避开 SmartScreen"未知发布者"提示（对杀软的拦截没有作用）
powershell -NoProfile -Command "$c = Get-ChildItem Cert:\CurrentUser\My | Where-Object FriendlyName -eq 'KeyLens Code Signing' | Select-Object -First 1; if ($c) { Set-AuthenticodeSignature -FilePath 'dist\KeyLens\KeyLens.exe' -Cert $c | Out-Null; echo '已签名' } else { echo '未找到签名证书（tools\codesign.pfx 未导入），跳过签名' }"

rem 安装到用户目录：复制出来的文件带的是目标目录的完整性标签，打包 exe 才能正常写数据库
set "TARGET=%LOCALAPPDATA%\Programs\KeyLens"
if exist "dist\KeyLens\KeyLens.exe" (
    if exist "%TARGET%" rmdir /s /q "%TARGET%"
    xcopy /e /i /y "dist\KeyLens" "%TARGET%" >nul
    echo.
    echo 打包完成并已安装：%TARGET%\KeyLens.exe
    echo 直接双击它运行即可；dist\KeyLens 里那份留着存档，不要从工作目录里直接跑。
) else (
    echo.
    echo 打包失败，请检查上方日志。
)
pause
