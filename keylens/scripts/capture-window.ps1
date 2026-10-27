# KeyLens 窗口截图：抓取正在运行的 KeyLens 窗口，存成 PNG。
#
# 用法（PowerShell）：
#   powershell -ExecutionPolicy Bypass -File keylens\scripts\capture-window.ps1
#   powershell -ExecutionPolicy Bypass -File keylens\scripts\capture-window.ps1 -Out preview.png
#
# 说明：它只做屏幕位图拷贝，不注入进程、不读取程序内存。截图里会包含窗口内容
# （也就是你自己的按键统计数字），注意别把截图发到不该发的地方。

param(
    [string]$Out = "keylens-window.png",
    [string]$TitleLike = "KeyLens"
)

Add-Type -AssemblyName System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinApi {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
}
"@

$proc = Get-Process |
    Where-Object { $_.MainWindowTitle -like "*$TitleLike*" } |
    Select-Object -First 1

if (-not $proc) {
    Write-Error "没找到标题包含 '$TitleLike' 的窗口。请先启动 KeyLens（python -m keylens.main）。"
    exit 1
}

$handle = $proc.MainWindowHandle
if ($handle -eq [IntPtr]::Zero) {
    Write-Error "找到了进程 $($proc.ProcessName)，但它没有可见主窗口。"
    exit 1
}

[void][WinApi]::SetForegroundWindow($handle)
Start-Sleep -Milliseconds 400

$rect = New-Object WinApi+RECT
if (-not [WinApi]::GetWindowRect($handle, [ref]$rect)) {
    Write-Error "GetWindowRect 调用失败。"
    exit 1
}

$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) {
    Write-Error "窗口尺寸异常：${width}x${height}"
    exit 1
}

$bitmap = New-Object System.Drawing.Bitmap $width, $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
$graphics.Dispose()

$target = [System.IO.Path]::GetFullPath($Out)
$bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()

Write-Output "已保存窗口截图：$target (${width}x${height})"
