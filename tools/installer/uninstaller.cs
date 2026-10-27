using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace KeyLensUninstall
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            bool silent = false;
            bool fromTemp = false;
            string dir = null;
            foreach (string a in args)
            {
                string lower = a.ToLowerInvariant();
                if (lower == "/s" || lower == "--silent") silent = true;
                else if (lower == "/from-temp") fromTemp = true;
                else if (lower.StartsWith("/dir=")) dir = a.Substring(5).Trim('"');
            }

            if (string.IsNullOrEmpty(dir))
            {
                dir = Path.GetDirectoryName(Application.ExecutablePath);
            }

            if (!fromTemp)
            {
                // copy ourselves to TEMP and run from there, so the install folder can be deleted
                string temp = Path.Combine(Path.GetTempPath(), "KeyLens-uninstall.exe");
                try
                {
                    File.Copy(Application.ExecutablePath, temp, true);
                }
                catch
                {
                    temp = Application.ExecutablePath;
                }
                string arguments = "/from-temp /dir=\"" + dir + "\"";
                if (silent) arguments += " /S";
                try
                {
                    Process.Start(temp, arguments);
                }
                catch
                {
                    return 1;
                }
                return 0;
            }

            if (!silent)
            {
                DialogResult answer = MessageBox.Show(
                    "确定要卸载 KeyLens 吗？\r\n\r\n" +
                    "程序文件、开始菜单 / 桌面快捷方式和卸载信息都会被删除。\r\n" +
                    "%LOCALAPPDATA%\\KeyLens 里的按键历史数据会保留（需要的话请手动删除）。",
                    "卸载 KeyLens", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (answer != DialogResult.Yes)
                {
                    return 0;
                }
            }

            // stop the app
            foreach (Process p in Process.GetProcessesByName("KeyLens"))
            {
                try { p.Kill(); p.WaitForExit(3000); }
                catch { }
            }
            Thread.Sleep(300);

            // shortcuts
            string startMenu = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            TryDelete(Path.Combine(startMenu, "KeyLens.lnk"));
            TryDelete(Path.Combine(startMenu, "卸载 KeyLens.lnk"));
            TryDelete(Path.Combine(desktop, "KeyLens.lnk"));

            // Add/Remove entry
            try
            {
                Registry.CurrentUser.DeleteSubKeyTree(
                    @"Software\Microsoft\Windows\CurrentVersion\Uninstall\KeyLens", false);
            }
            catch { }

            // files
            bool removed = false;
            for (int attempt = 0; attempt < 3 && !removed; attempt++)
            {
                try
                {
                    if (Directory.Exists(dir))
                    {
                        Directory.Delete(dir, true);
                    }
                    removed = true;
                }
                catch
                {
                    Thread.Sleep(400);
                }
            }

            // delete this exe after we exit
            string self = Application.ExecutablePath;
            try
            {
                ProcessStartInfo info = new ProcessStartInfo("cmd.exe",
                    "/c ping -n 2 127.0.0.1 >nul & del /f /q \"" + self + "\"");
                info.CreateNoWindow = true;
                info.UseShellExecute = false;
                info.WindowStyle = ProcessWindowStyle.Hidden;
                Process.Start(info);
            }
            catch { }

            if (!silent)
            {
                MessageBox.Show(
                    removed
                        ? "KeyLens 已卸载。\r\n\r\n按键历史数据仍保留在 %LOCALAPPDATA%\\KeyLens。"
                        : "程序文件未能全部删除（可能仍在运行），请手动删除：\r\n" + dir,
                    "卸载 KeyLens", MessageBoxButtons.OK,
                    removed ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
            }
            return 0;
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (File.Exists(path)) File.Delete(path);
            }
            catch { }
        }
    }
}
