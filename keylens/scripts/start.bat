@echo off
rem KeyLens 启动脚本：优先用 pythonw（不弹控制台窗口），失败则回退 python。
setlocal
cd /d "%~dp0.."
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m keylens.main %*
) else (
    python -m keylens.main %*
)
endlocal
