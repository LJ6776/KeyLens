@echo off
rem Build the KeyLens SETUP package:
rem   onedir payload (PyInstaller) + uninstaller.exe (csc) -> payload.zip
rem   -> C# wizard installer (csc) -> sign -> D:\HC\KeyLens\release
rem ASCII-only on purpose: batch files here are read as ANSI, non-ASCII would garble.
setlocal
cd /d "%~dp0.."
set "REL=D:\HC\KeyLens\release"
set "PAYLOAD=tools\installer\payload.zip"
set "UNINST=tools\installer\uninstaller.exe"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

set PYTHON=python
where %PYTHON% >nul 2>nul || set PYTHON=D:\RJ\Python\3.14.3\python.exe

echo [1/6] building onedir payload ...
%PYTHON% -m PyInstaller --noconfirm --clean --onedir --windowed --name KeyLens ^
    --icon tools\app.ico ^
    --version-file tools\exe-version.py ^
    --add-data "tools\app.ico;." ^
    --paths "%CD%" ^
    keylens\run.py
if not exist "dist\KeyLens\KeyLens.exe" goto :failed

echo [2/6] signing payload exe ...
powershell -NoProfile -Command "$c = Get-ChildItem Cert:\CurrentUser\My | Where-Object FriendlyName -eq 'KeyLens Code Signing' | Select-Object -First 1; if ($c) { Set-AuthenticodeSignature -FilePath 'dist\KeyLens\KeyLens.exe' -Cert $c | Out-Null; echo 'signed' } else { echo 'no cert, skipped' }"

echo [3/6] compiling uninstaller ...
"%CSC%" /nologo /codepage:65001 /target:winexe /optimize+ ^
    /out:"%UNINST%" /win32icon:"tools\app.ico" ^
    /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll ^
    "tools\installer\uninstaller.cs"
if not exist "%UNINST%" goto :failed

echo [4/6] packing payload ...
if exist "%PAYLOAD%" del /q "%PAYLOAD%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\KeyLens\*' -DestinationPath '%PAYLOAD%' -CompressionLevel Optimal"
if not exist "%PAYLOAD%" goto :failed

echo [5/6] compiling installer ...
if not exist "%REL%" mkdir "%REL%"
if exist "%REL%\KeyLens-1.0.0-setup.exe" del /q "%REL%\KeyLens-1.0.0-setup.exe"
"%CSC%" /nologo /codepage:65001 /target:winexe /optimize+ ^
    /out:"%REL%\KeyLens-1.0.0-setup.exe" ^
    /win32icon:"tools\app.ico" ^
    /resource:"%PAYLOAD%",payload.zip ^
    /resource:"%UNINST%",uninstaller.exe ^
    /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll ^
    /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll ^
    "tools\installer\installer.cs"
if not exist "%REL%\KeyLens-1.0.0-setup.exe" goto :failed

echo [6/6] signing setup exe ...
powershell -NoProfile -Command "$c = Get-ChildItem Cert:\CurrentUser\My | Where-Object FriendlyName -eq 'KeyLens Code Signing' | Select-Object -First 1; if ($c) { Set-AuthenticodeSignature -FilePath '%REL%\KeyLens-1.0.0-setup.exe' -Cert $c | Out-Null; echo 'signed' } else { echo 'no cert, skipped' }"

del /q "%PAYLOAD%" >nul 2>nul
del /q "%UNINST%" >nul 2>nul
echo.
echo Setup package: %REL%\KeyLens-1.0.0-setup.exe
exit /b 0

:failed
echo.
echo BUILD FAILED - see messages above.
exit /b 1
