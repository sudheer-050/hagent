using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Threading;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal static class Launcher {
    static string Data = Environment.GetEnvironmentVariable("HAGENT_DESKTOP_DATA") ??
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "HagentTest");
    static string Endpoint = Path.Combine(Data, "desktop-endpoint.json");
    static string ReadUrl() {
        var values = new JavaScriptSerializer().Deserialize<System.Collections.Generic.Dictionary<string,int>>(File.ReadAllText(Endpoint));
        int port = values["port"];
        if (port < 1024 || port > 65535) throw new Exception("Invalid saved port");
        return "http://127.0.0.1:" + port + "/";
    }
    static void Open(string url) { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
    [STAThread] static int Main(string[] args) {
        bool owns;
        using (var mutex = new Mutex(true, "Local\\HagentTestDesktop", out owns)) {
            try {
                if (!owns) { Open(ReadUrl()); return 0; }
                Application.EnableVisualStyles();
                Directory.CreateDirectory(Data);
                if (File.Exists(Endpoint)) File.Delete(Endpoint);
                string root = AppDomain.CurrentDomain.BaseDirectory;
                var info = new ProcessStartInfo(Path.Combine(root, "runtime", "python.exe"), "-m hagent.desktop") {
                    WorkingDirectory = Data, UseShellExecute = false, CreateNoWindow = true,
                    RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true
                };
                info.EnvironmentVariables["HAGENT_DESKTOP_DATA"] = Data;
                using (var log = new StreamWriter(Path.Combine(Data, "desktop.log"), true))
                using (var process = new Process { StartInfo = info }) {
                    log.AutoFlush = true;
                    DataReceivedEventHandler write = (sender, e) => { if (e.Data != null) lock(log) log.WriteLine(e.Data); };
                    process.OutputDataReceived += write; process.ErrorDataReceived += write;
                    process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
                    try {
                        string url = null;
                        for (int i=0; i<120; i++) {
                            if (process.HasExited) throw new Exception("Hagent stopped during startup. See " + Path.Combine(Data, "desktop.log"));
                            try {
                                string candidate = ReadUrl();
                                var request = (HttpWebRequest)WebRequest.Create(candidate + "healthz");
                                request.Timeout = 500; request.Proxy = null;
                                using (var response = request.GetResponse()) { url = candidate; break; }
                            } catch { Thread.Sleep(250); }
                        }
                        if (url == null) throw new Exception("Hagent did not become ready. See desktop.log in " + Data);
                        if (Array.IndexOf(args, "--smoke-test") >= 0) return 0;
                        Open(url);
                        using (var tray = new NotifyIcon { Icon = SystemIcons.Application, Text = "Hagent test", Visible = true })
                        using (var menu = new ContextMenuStrip()) {
                            menu.Items.Add("Open Hagent", null, (s,e) => Open(url));
                            menu.Items.Add("Exit Hagent", null, (s,e) => Application.Exit());
                            tray.ContextMenuStrip = menu;
                            tray.DoubleClick += (s,e) => Open(url);
                            Application.Run();
                            tray.Visible = false;
                        }
                    } finally {
                        if (!process.HasExited) {
                            process.StandardInput.WriteLine("stop"); process.StandardInput.Close();
                            if (!process.WaitForExit(15000)) process.Kill();
                        }
                    }
                }
                return 0;
            } catch (Exception ex) {
                if (Array.IndexOf(args, "--smoke-test") < 0) MessageBox.Show(ex.Message, "Hagent", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            } finally { if (owns) mutex.ReleaseMutex(); }
        }
    }
}
