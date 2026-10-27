using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

namespace KeyLensSetup
{
    internal static class Program
    {
        internal const string Version = "1.0.0";

        internal static string DefaultDir
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    @"Programs\KeyLens");
            }
        }

        [STAThread]
        private static int Main(string[] args)
        {
            bool silent = false;
            string dir = null;
            foreach (string a in args)
            {
                string lower = a.ToLowerInvariant();
                if (lower == "/s" || lower == "--silent")
                {
                    silent = true;
                }
                else if (lower.StartsWith("/d="))
                {
                    dir = a.Substring(3).Trim('"');
                }
                else if (lower.StartsWith("/dir="))
                {
                    dir = a.Substring(5).Trim('"');
                }
            }
            if (string.IsNullOrEmpty(dir)) dir = DefaultDir;

            if (silent)
            {
                try
                {
                    Installer.Install(dir, true, true, null);
                    return 0;
                }
                catch (Exception ex)
                {
                    try
                    {
                        File.WriteAllText(Path.Combine(Path.GetTempPath(), "keylens-setup-error.txt"),
                            ex.ToString(), Encoding.UTF8);
                    }
                    catch { }
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new Wizard(dir));
            return 0;
        }

        internal static Stream OpenResource(string name)
        {
            return Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
        }
    }

    internal sealed class Wizard : Form
    {
        private const string EulaText =
            "KeyLens 1.0.0 使用许可协议\r\n" +
            "\r\n" +
            "1. 用途\r\n" +
            "   本软件是一款本机自用的按键 / 剪贴板统计工具，用于查看自己的输入习惯\r\n" +
            "   （热力图、按键排行、复制粘贴次数、日报 / 汇总报告）。\r\n" +
            "\r\n" +
            "2. 数据与隐私\r\n" +
            "   所有记录只写入本机 %LOCALAPPDATA%\\KeyLens 目录，程序不联网、不上传、\r\n" +
            "   不包含任何遥测。密码框、密码管理器等敏感窗口会被自动掩码，只记类型不记内容。\r\n" +
            "   卸载程序不会删除该目录下的历史数据，需要时请自行删除。\r\n" +
            "\r\n" +
            "3. 键盘钩子\r\n" +
            "   程序会安装全局键盘钩子（WH_KEYBOARD_LL）才能统计按键。这是本软件能工作的前提。\r\n" +
            "   请在了解这一点后使用，并只安装在你自己的电脑上。\r\n" +
            "\r\n" +
            "4. 使用限制\r\n" +
            "   严禁在未经他人同意的情况下安装到他人设备，或用于窃取他人输入内容；\r\n" +
            "   由此产生的一切后果由使用者自行承担。\r\n" +
            "\r\n" +
            "5. 免责声明\r\n" +
            "   本软件按“现状”提供，不对适用性、稳定性作任何明示或暗示担保。\r\n" +
            "   因使用本软件造成的数据丢失、系统异常等风险由使用者自行承担。\r\n" +
            "\r\n" +
            "6. 其他\r\n" +
            "   继续安装即表示你已阅读、理解并同意以上条款；如不同意，请点“取消”退出。";

        private readonly Panel[] _pages = new Panel[5];
        private int _index;

        private readonly Label _title = new Label();
        private readonly Button _back = new Button();
        private readonly Button _next = new Button();
        private readonly Button _cancel = new Button();

        private readonly CheckBox _agree = new CheckBox();
        private readonly TextBox _dirBox = new TextBox();
        private readonly CheckBox _desktopShortcut = new CheckBox();
        private readonly CheckBox _menuShortcut = new CheckBox();
        private readonly ProgressBar _bar = new ProgressBar();
        private readonly Label _progressText = new Label();
        private readonly CheckBox _runNow = new CheckBox();
        private readonly Label _finishInfo = new Label();
        private string _dir;

        public Wizard(string defaultDir)
        {
            _dir = defaultDir;
            Text = "KeyLens 1.0.0 安装向导";
            ClientSize = new Size(620, 450);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Microsoft YaHei UI", 9f);
            BackColor = Color.White;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
            catch { }

            _title.Location = new Point(20, 14);
            _title.Size = new Size(580, 26);
            _title.Font = new Font(Font.FontFamily, 11.5f, FontStyle.Bold);
            Controls.Add(_title);

            BuildWelcomePage();
            BuildDirectoryPage();
            BuildTasksPage();
            BuildProgressPage();
            BuildFinishPage();

            foreach (Panel p in _pages)
            {
                p.Location = new Point(0, 46);
                p.Size = new Size(620, 330);
                p.BackColor = Color.White;
                p.Visible = false;
                Controls.Add(p);
            }

            _back.Text = "< 上一步";
            _back.Location = new Point(304, 394);
            _back.Size = new Size(92, 32);
            _back.Click += delegate { GoBack(); };
            Controls.Add(_back);

            _next.Text = "下一步 >";
            _next.Location = new Point(402, 394);
            _next.Size = new Size(92, 32);
            _next.Click += delegate { GoNext(); };
            Controls.Add(_next);

            _cancel.Text = "取消";
            _cancel.Location = new Point(500, 394);
            _cancel.Size = new Size(92, 32);
            _cancel.Click += delegate { CancelWizard(); };
            Controls.Add(_cancel);

            ShowPage(0);
        }

        // ---------- pages ----------
        private void BuildWelcomePage()
        {
            Panel page = new Panel();
            _pages[0] = page;

            Label intro = new Label();
            intro.Text = "欢迎使用 KeyLens 1.0.0 安装向导。\r\n\r\n" +
                         "本向导会把 KeyLens 安装到你的电脑上，并创建开始菜单 / 桌面快捷方式。\r\n" +
                         "安装前请阅读下面的许可协议：";
            intro.Location = new Point(20, 4);
            intro.Size = new Size(560, 96);
            page.Controls.Add(intro);

            RichTextBox eula = new RichTextBox();
            eula.Text = EulaText;
            eula.Location = new Point(20, 104);
            eula.Size = new Size(560, 168);
            eula.ReadOnly = true;
            eula.BorderStyle = BorderStyle.FixedSingle;
            eula.BackColor = Color.FromArgb(250, 250, 250);
            eula.Font = new Font("Microsoft YaHei UI", 8.5f);
            page.Controls.Add(eula);

            _agree.Text = "我已阅读并同意上述许可协议";
            _agree.Location = new Point(20, 282);
            _agree.AutoSize = true;
            _agree.CheckedChanged += delegate { if (_index == 0) _next.Enabled = _agree.Checked; };
            page.Controls.Add(_agree);
        }

        private void BuildDirectoryPage()
        {
            Panel page = new Panel();
            _pages[1] = page;

            Label tip = new Label();
            tip.Text = "请选择 KeyLens 的安装位置，然后点“下一步”：";
            tip.Location = new Point(20, 20);
            tip.AutoSize = true;
            page.Controls.Add(tip);

            _dirBox.Text = _dir;
            _dirBox.Location = new Point(20, 52);
            _dirBox.Size = new Size(470, 26);
            page.Controls.Add(_dirBox);

            Button browse = new Button();
            browse.Text = "浏览...";
            browse.Location = new Point(500, 51);
            browse.Size = new Size(80, 28);
            browse.Click += delegate
            {
                FolderBrowserDialog dialog = new FolderBrowserDialog();
                dialog.Description = "选择 KeyLens 的安装文件夹";
                dialog.SelectedPath = _dirBox.Text;
                if (dialog.ShowDialog(this) == DialogResult.OK)
                {
                    _dirBox.Text = dialog.SelectedPath;
                }
            };
            page.Controls.Add(browse);

            Label note = new Label();
            note.Text = "需要约 80 MB 磁盘空间。\r\n" +
                        "注意：不要安装到工程源码目录里——部分安全软件会拦那个目录下的 exe 写数据库。";
            note.Location = new Point(20, 92);
            note.Size = new Size(570, 44);
            note.ForeColor = Color.FromArgb(110, 110, 110);
            page.Controls.Add(note);

            Label hint = new Label();
            hint.Text = "安装完成后，程序本体在这个文件夹里，数据仍然保存在 %LOCALAPPDATA%\\KeyLens。";
            hint.Location = new Point(20, 146);
            hint.Size = new Size(570, 40);
            hint.ForeColor = Color.FromArgb(110, 110, 110);
            page.Controls.Add(hint);
        }

        private void BuildTasksPage()
        {
            Panel page = new Panel();
            _pages[2] = page;

            Label tip = new Label();
            tip.Text = "选择要创建的快捷方式：";
            tip.Location = new Point(20, 20);
            tip.AutoSize = true;
            page.Controls.Add(tip);

            _desktopShortcut.Text = "创建桌面快捷方式";
            _desktopShortcut.Location = new Point(38, 58);
            _desktopShortcut.AutoSize = true;
            _desktopShortcut.Checked = true;
            page.Controls.Add(_desktopShortcut);

            _menuShortcut.Text = "创建开始菜单快捷方式（含“卸载 KeyLens”）";
            _menuShortcut.Location = new Point(38, 90);
            _menuShortcut.AutoSize = true;
            _menuShortcut.Checked = true;
            page.Controls.Add(_menuShortcut);

            Label note = new Label();
            note.Text = "安装完成后，可以在“设置 → 应用 → 已安装的应用”里卸载 KeyLens。";
            note.Location = new Point(20, 140);
            note.Size = new Size(570, 24);
            note.ForeColor = Color.FromArgb(110, 110, 110);
            page.Controls.Add(note);
        }

        private void BuildProgressPage()
        {
            Panel page = new Panel();
            _pages[3] = page;

            Label tip = new Label();
            tip.Text = "正在安装 KeyLens，请稍候……";
            tip.Location = new Point(20, 20);
            tip.AutoSize = true;
            page.Controls.Add(tip);

            _bar.Location = new Point(20, 64);
            _bar.Size = new Size(560, 24);
            _bar.Minimum = 0;
            _bar.Maximum = 100;
            page.Controls.Add(_bar);

            _progressText.Text = "";
            _progressText.Location = new Point(20, 100);
            _progressText.Size = new Size(560, 24);
            page.Controls.Add(_progressText);
        }

        private void BuildFinishPage()
        {
            Panel page = new Panel();
            _pages[4] = page;

            Label done = new Label();
            done.Text = "安装完成！";
            done.Location = new Point(20, 16);
            done.AutoSize = true;
            done.Font = new Font(Font.FontFamily, 11f, FontStyle.Bold);
            page.Controls.Add(done);

            _finishInfo.Text = "";
            _finishInfo.Location = new Point(20, 52);
            _finishInfo.Size = new Size(570, 96);
            page.Controls.Add(_finishInfo);

            _runNow.Text = "立即运行 KeyLens";
            _runNow.Location = new Point(22, 158);
            _runNow.AutoSize = true;
            _runNow.Checked = true;
            page.Controls.Add(_runNow);

            Label note = new Label();
            note.Text = "提示：首次启动时若出现「打开文件 - 安全警告」，点“打开”即可。";
            note.Location = new Point(20, 196);
            note.Size = new Size(570, 24);
            note.ForeColor = Color.FromArgb(110, 110, 110);
            page.Controls.Add(note);
        }

        // ---------- navigation ----------
        private void ShowPage(int index)
        {
            _index = index;
            for (int i = 0; i < _pages.Length; i++)
            {
                _pages[i].Visible = (i == index);
            }
            _back.Enabled = index > 0 && index < 3;
            _cancel.Enabled = index < 3;
            switch (index)
            {
                case 0:
                    _title.Text = "欢迎使用 KeyLens 安装向导";
                    _next.Text = "下一步 >";
                    _next.Enabled = _agree.Checked;
                    break;
                case 1:
                    _title.Text = "选择安装位置";
                    _next.Text = "下一步 >";
                    _next.Enabled = true;
                    break;
                case 2:
                    _title.Text = "选择附加任务";
                    _next.Text = "安装";
                    _next.Enabled = true;
                    break;
                case 3:
                    _title.Text = "正在安装";
                    _next.Enabled = false;
                    break;
                case 4:
                    _title.Text = "安装完成";
                    _next.Text = "完成";
                    _next.Enabled = true;
                    break;
            }
            if (index == 1) _dirBox.Text = _dir;
        }

        private void GoBack()
        {
            if (_index > 0 && _index < 3) ShowPage(_index - 1);
        }

        private void GoNext()
        {
            if (_index == 0)
            {
                if (!_agree.Checked) return;
                ShowPage(1);
                return;
            }
            if (_index == 1)
            {
                string chosen = _dirBox.Text.Trim();
                if (chosen.Length == 0)
                {
                    MessageBox.Show("请填写安装位置。", "KeyLens 安装向导",
                        MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                try
                {
                    if (!Path.IsPathRooted(chosen))
                    {
                        MessageBox.Show("请填写完整路径（例如 D:\\Tools\\KeyLens）。", "KeyLens 安装向导",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning);
                        return;
                    }
                }
                catch { }
                _dir = chosen;
                ShowPage(2);
                return;
            }
            if (_index == 2)
            {
                StartInstall();
                return;
            }
            if (_index == 4)
            {
                if (_runNow.Checked)
                {
                    try { Process.Start(Path.Combine(_dir, "KeyLens.exe")); }
                    catch (Exception ex)
                    {
                        MessageBox.Show("启动失败：" + ex.Message, "KeyLens",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }
                }
                Close();
            }
        }

        private void CancelWizard()
        {
            if (_index < 3)
            {
                DialogResult answer = MessageBox.Show("确定要取消安装吗？", "KeyLens 安装向导",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (answer == DialogResult.Yes) Close();
            }
        }

        private void StartInstall()
        {
            ShowPage(3);
            _bar.Value = 0;
            _progressText.Text = "准备中...";
            Application.DoEvents();
            try
            {
                Installer.Install(_dir, _desktopShortcut.Checked, _menuShortcut.Checked,
                    delegate(int pct, string text)
                    {
                        _bar.Value = Math.Max(0, Math.Min(100, pct));
                        _progressText.Text = text;
                        Application.DoEvents();
                    });
                _finishInfo.Text = "KeyLens 已安装到：\r\n" + _dir +
                    "\r\n\r\n快捷键：Ctrl+Alt+K 显示 / 隐藏主界面；关闭按钮 = 退出程序。";
                ShowPage(4);
            }
            catch (Exception ex)
            {
                MessageBox.Show("安装失败：\r\n" + ex.Message, "KeyLens 安装向导",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                ShowPage(2);
            }
        }
    }

    internal static class Installer
    {
        internal static void Install(string dir, bool desktopShortcut, bool startMenuShortcut,
            Action<int, string> progress)
        {
            Action<int, string> report = progress ?? delegate { };

            // 1) stop a running instance so the files are not locked
            report(2, "正在关闭运行中的 KeyLens...");
            foreach (Process p in Process.GetProcessesByName("KeyLens"))
            {
                try { p.Kill(); p.WaitForExit(3000); }
                catch { }
            }

            // 2) prepare the target directory
            report(6, "正在准备安装目录...");
            if (Directory.Exists(dir))
            {
                TryStopUninstaller();
                Directory.Delete(dir, true);
            }
            Directory.CreateDirectory(dir);

            // 3) unpack the embedded payload
            Stream res = Program.OpenResource("payload.zip");
            if (res == null) throw new InvalidOperationException("安装包内部缺少 payload.zip");
            using (MemoryStream ms = new MemoryStream())
            {
                res.CopyTo(ms);
                ms.Position = 0;
                using (ZipArchive zip = new ZipArchive(ms, ZipArchiveMode.Read))
                {
                    int total = zip.Entries.Count;
                    int done = 0;
                    foreach (ZipArchiveEntry entry in zip.Entries)
                    {
                        string rel = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        string full = Path.Combine(dir, rel);
                        if (rel.EndsWith(Path.DirectorySeparatorChar.ToString()) || entry.Name.Length == 0)
                        {
                            Directory.CreateDirectory(full);
                        }
                        else
                        {
                            string parent = Path.GetDirectoryName(full);
                            if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                            entry.ExtractToFile(full, true);
                        }
                        done++;
                        if (total > 0 && (done % 25 == 0 || done == total))
                        {
                            report(6 + done * 74 / total, "正在解压文件 " + done + " / " + total + " ...");
                        }
                    }
                }
            }

            // 4) uninstaller
            report(84, "正在写入卸载程序...");
            Stream uni = Program.OpenResource("uninstaller.exe");
            if (uni != null)
            {
                string uniPath = Path.Combine(dir, "uninstall.exe");
                using (FileStream fs = new FileStream(uniPath, FileMode.Create, FileAccess.Write))
                {
                    uni.CopyTo(fs);
                }
            }

            // 5) shortcuts
            report(90, "正在创建快捷方式...");
            string startMenu = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
            if (startMenuShortcut)
            {
                CreateShortcut(Path.Combine(startMenu, "KeyLens.lnk"),
                    Path.Combine(dir, "KeyLens.exe"), dir);
                string uniPath = Path.Combine(dir, "uninstall.exe");
                if (File.Exists(uniPath))
                {
                    CreateShortcut(Path.Combine(startMenu, "卸载 KeyLens.lnk"), uniPath, dir);
                }
            }
            if (desktopShortcut)
            {
                CreateShortcut(Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                        "KeyLens.lnk"),
                    Path.Combine(dir, "KeyLens.exe"), dir);
            }

            // 6) Add/Remove Programs entry
            report(96, "正在写入卸载信息...");
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Uninstall\KeyLens"))
            {
                key.SetValue("DisplayName", "KeyLens 按键记录器");
                key.SetValue("DisplayVersion", Program.Version);
                key.SetValue("Publisher", "本机自用");
                key.SetValue("InstallLocation", dir);
                key.SetValue("DisplayIcon", Path.Combine(dir, "KeyLens.exe"));
                key.SetValue("UninstallString", "\"" + Path.Combine(dir, "uninstall.exe") + "\"");
                key.SetValue("QuietUninstallString",
                    "\"" + Path.Combine(dir, "uninstall.exe") + "\" /S");
                try
                {
                    long bytes = 0;
                    foreach (FileInfo f in new DirectoryInfo(dir).GetFiles("*", SearchOption.AllDirectories))
                    {
                        bytes += f.Length;
                    }
                    key.SetValue("EstimatedSize", (int)(bytes / 1024), RegistryValueKind.DWord);
                }
                catch { }
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
            report(100, "安装完成。");
        }

        private static void TryStopUninstaller()
        {
            foreach (Process p in Process.GetProcessesByName("uninstall"))
            {
                try { p.Kill(); }
                catch { }
            }
        }

        private static void CreateShortcut(string lnkPath, string target, string workDir)
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) return;
            object shell = Activator.CreateInstance(shellType);
            object lnk = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod,
                null, shell, new object[] { lnkPath });
            Type lnkType = lnk.GetType();
            lnkType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, lnk, new object[] { target });
            lnkType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, lnk, new object[] { workDir });
            lnkType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, lnk,
                new object[] { Path.Combine(workDir, "KeyLens.exe") + ",0" });
            lnkType.InvokeMember("Save", BindingFlags.InvokeMethod, null, lnk, null);
        }
    }
}
