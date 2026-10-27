@echo off
rem 清掉 KeyLens 的运行期垃圾。删除前会逐项列出，你确认后再动手；不会碰任何源码。
setlocal
cd /d "%~dp0.."

echo 即将删除（只删运行期产物，不碰源码）：
if exist "keylens\debug.log"                 echo   - keylens\debug.log                  （第三方程序写进来的日志）
if exist "keylens\keylens\keylens-previews"  echo   - keylens\keylens\keylens-previews\  （旧位置的预览）
if exist "keylens\previews"                  echo   - keylens\previews\                  （--demo / 预览生成物）
echo   - keylens\**\__pycache__\             （Python 字节码缓存，会自动重建）
if exist "build"                             echo   - build\                             （PyInstaller 中间产物）
if exist "dist"                              echo   - dist\  ⚠ 本脚本不会删，需要时请手动删（里面有你的 exe）
echo.

set /p CONFIRM=确认删除以上内容？输入 yes 回车：
if /i not "%CONFIRM%"=="yes" (
    echo 已取消，什么都没删。
    pause
    exit /b 0
)

if exist "keylens\debug.log"                del /q "keylens\debug.log"
if exist "keylens\keylens\keylens-previews" rmdir /s /q "keylens\keylens\keylens-previews"
if exist "keylens\previews"                 rmdir /s /q "keylens\previews"
for /d /r "keylens" %%D in (__pycache__) do @if exist "%%D" rmdir /s /q "%%D"
if exist "build"                            rmdir /s /q "build"

echo.
echo 清理完成。
pause
