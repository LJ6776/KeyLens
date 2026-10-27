@echo off
rem 单文件 exe 打包（PyInstaller --onefile）。
rem 产物：dist\KeyLens.exe，并复制到 release\KeyLens.exe（一个文件，可直接拷走）。
rem
rem 和 tools\make-exe.bat 的区别：
rem   onedir（make-exe.bat） = 一个文件夹，启动快；安装到 %LOCALAPPDATA%\Programs\KeyLens
rem   onefile（本脚本）       = 单个 exe，启动时会解压到临时目录，慢 1-3 秒；个别杀软会拦解压
setlocal
cd /d "%~dp0.."

set PYTHON=python
where %PYTHON% >nul 2>nul || set PYTHON=python
set "OUT=%CD%\release"

%PYTHON% -m PyInstaller --noconfirm --clean --onefile --windowed --name KeyLens ^
    --icon tools\app.ico ^
    --version-file tools\exe-version.py ^
    --add-data "tools\app.ico;." ^
    --paths "%CD%" ^
    keylens\run.py

rem 本地自签名
powershell -NoProfile -Command "$c = Get-ChildItem Cert:\CurrentUser\My | Where-Object FriendlyName -eq 'KeyLens Code Signing' | Select-Object -First 1; if ($c) { Set-AuthenticodeSignature -FilePath 'dist\KeyLens.exe' -Cert $c | Out-Null; echo '已签名' } else { echo '未找到签名证书（tools\codesign.pfx 未导入），跳过签名' }"

if exist "dist\KeyLens.exe" (
    if not exist "%OUT%" mkdir "%OUT%"
    copy /y "dist\KeyLens.exe" "%OUT%\KeyLens.exe" >nul
    echo.
    echo 打包完成：%OUT%\KeyLens.exe（单文件，可直接拷到别处）
    echo 注意：不要把它放在工程目录里运行，部分安全软件会拦它写数据库。
) else (
    echo.
    echo 打包失败，请检查上方日志。
)
pause
