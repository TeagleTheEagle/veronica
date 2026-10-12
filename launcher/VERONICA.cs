// VERONICA.exe: opens VERONICA's window from the folder it sits in (the veronica folder).
// It runs .venv\Scripts\pythonw.exe app.py and nothing else; it reads no keys and changes nothing.
// If VERONICA stops with an error, the end of veronica.log is shown in a message box.
// (Typing or talking in a console instead: "Type to VERONICA.cmd" and "Talk to VERONICA.cmd".)
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("VERONICA")]
[assembly: AssemblyProduct("VERONICA")]
[assembly: AssemblyDescription("Agent Veronica")]
[assembly: AssemblyVersion("1.1.0.0")]

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(folder, ".venv", "Scripts", "pythonw.exe");
        string app = Path.Combine(folder, "app.py");
        if (!File.Exists(python) || !File.Exists(app))
        {
            MessageBox.Show("VERONICA.exe must stay in the veronica folder, next to app.py and .venv.\n\nLooked in: " + folder,
                            "VERONICA", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }
        ProcessStartInfo start = new ProcessStartInfo(python, "\"" + app + "\"");
        start.WorkingDirectory = folder;
        start.UseShellExecute = false;
        try
        {
            using (Process veronica = Process.Start(start))
            {
                veronica.WaitForExit();
                if (veronica.ExitCode != 0) ShowLog(folder, veronica.ExitCode);
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

    static void ShowLog(string folder, int code)
    {
        string log = Path.Combine(folder, "veronica.log");
        string tail = File.Exists(log) ? string.Join("\n", File.ReadAllLines(log).Reverse().Take(12).Reverse()) : "(no log)";
        MessageBox.Show("VERONICA stopped with an error (" + code + ").\n\n" + tail, "VERONICA",
                        MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
