// VERONICA.exe of the installed app (%LOCALAPPDATA%\VERONICA\VERONICA.exe; built by tools\build_release.py).
// It reads current.json beside it, and runs releases\<id>\python\pythonw.exe -B releases\<id>\app\app.py.
// It reads no keys and changes nothing. If VERONICA stops with an error, the end of veronica.log is shown.
// With no current.json, or a release that is missing, it says so and asks for the Setup to be run again.
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Windows.Forms;

[assembly: AssemblyTitle("VERONICA")]
[assembly: AssemblyProduct("VERONICA")]
[assembly: AssemblyCompany("Teague Industries")]
[assembly: AssemblyDescription("VERONICA")]
[assembly: AssemblyVersion("@VERSION_QUAD@")]
[assembly: AssemblyFileVersion("@VERSION_QUAD@")]

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string home = AppDomain.CurrentDomain.BaseDirectory;
        string current = Path.Combine(home, "current.json");
        string release = null;
        if (File.Exists(current))
        {
            Match m = Regex.Match(File.ReadAllText(current), "\"release\"\\s*:\\s*\"([A-Za-z0-9._-]+)\"");
            if (m.Success) release = m.Groups[1].Value;
        }
        string folder = release == null ? null : Path.Combine(home, "releases", release);
        string python = folder == null ? null : Path.Combine(folder, "python", "pythonw.exe");
        string app = folder == null ? null : Path.Combine(folder, "app", "app.py");
        if (python == null || !File.Exists(python) || !File.Exists(app))
        {
            MessageBox.Show("VERONICA's program is missing or incomplete in " + home +
                            ".\n\nRun the VERONICA Setup again to repair it. Your data is kept.",
                            "VERONICA", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }
        ProcessStartInfo start = new ProcessStartInfo(python, "-B \"" + app + "\"");
        start.WorkingDirectory = home;
        start.UseShellExecute = false;
        try
        {
            using (Process veronica = Process.Start(start))
            {
                veronica.WaitForExit();
                if (veronica.ExitCode != 0) ShowLog(home, veronica.ExitCode);
                return veronica.ExitCode;
            }
        }
        catch (Exception problem)
        {
            MessageBox.Show("VERONICA could not be started: " + problem.Message, "VERONICA",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    static void ShowLog(string home, int code)
    {
        string log = Path.Combine(home, "veronica.log");
        string tail = File.Exists(log) ? string.Join("\n", File.ReadAllLines(log).Reverse().Take(12).Reverse()) : "(no log)";
        MessageBox.Show("VERONICA stopped with an error (" + code + ").\n\n" + tail, "VERONICA",
                        MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
