"""Desktop alarm popups with Stop/Snooze buttons, degrading to a plain notification.

Each backend runs as a child process so the daemon keeps ticking while the popup
is open, and closing the alert simply terminates that process.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import sys

from .windows import powershell, powershell_command, ps_quote

WINDOWS_POPUP = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$global:result = 'dismiss'
$form = New-Object System.Windows.Forms.Form
$form.Text = 'tock'
$form.Icon = [System.Drawing.SystemIcons]::Information
$form.TopMost = $true
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.AutoScaleMode = 'Dpi'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$form.ClientSize = New-Object System.Drawing.Size(400, 160)

$icon = New-Object System.Windows.Forms.PictureBox
$icon.Image = [System.Drawing.SystemIcons]::Information.ToBitmap()
$icon.SetBounds(20, 22, 32, 32)
$form.Controls.Add($icon)

$heading = New-Object System.Windows.Forms.Label
$heading.Text = __TITLE__
$heading.Font = New-Object System.Drawing.Font('Segoe UI', 15, [System.Drawing.FontStyle]::Bold)
$heading.AutoEllipsis = $true
$heading.SetBounds(64, 16, 320, 36)
$form.Controls.Add($heading)

$detail = New-Object System.Windows.Forms.Label
$detail.Text = __MESSAGE__
$detail.AutoEllipsis = $true
$detail.SetBounds(66, 54, 318, 44)
$form.Controls.Add($detail)

$snooze = New-Object System.Windows.Forms.Button
$snooze.Text = 'Snooze'
$snooze.SetBounds(188, 110, 96, 34)
$snooze.Add_Click({ $global:result = 'snooze'; $form.Close() })
$form.Controls.Add($snooze)

$stop = New-Object System.Windows.Forms.Button
$stop.Text = 'Stop'
$stop.SetBounds(292, 110, 96, 34)
$stop.Add_Click({ $global:result = 'dismiss'; $form.Close() })
$form.Controls.Add($stop)
$form.AcceptButton = $stop

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = __TIMEOUT_MS__
$timer.Add_Tick({ $global:result = 'timeout'; $form.Close() })
$timer.Start()

$form.Add_Shown({ $form.Activate(); $stop.Focus() })
[void]$form.ShowDialog()
Write-Output $global:result
"""

MAC_POPUP = """on run argv
    set reply to display dialog (item 2 of argv) with title (item 1 of argv) buttons {"Snooze", "Stop"} default button "Stop" with icon caution giving up after ((item 3 of argv) as integer)
    if gave up of reply then return "timeout"
    if button returned of reply is "Snooze" then return "snooze"
    return "dismiss"
end run"""


INTERACTIVE_BACKENDS = ("windows popup", "osascript", "zenity")


def selected_notifier() -> str:
    if powershell():  # native Windows, and WSL where Windows owns the visible desktop
        return "windows popup"
    if sys.platform == "darwin" and shutil.which("osascript"):
        return "osascript"
    if sys.platform.startswith("linux"):
        for command in ("zenity", "notify-send"):
            if shutil.which(command):
                return command
    return "none"


class DesktopNotifier:
    """One alarm popup: ``show`` it, ``poll`` for a button press, ``close`` it."""

    def __init__(self, *, timeout: float = 300, backend: str | None = None) -> None:
        self.timeout = max(1, int(timeout))
        self.backend = backend or selected_notifier()
        self._process: subprocess.Popen | None = None
        self.error: str | None = None

    @property
    def active(self) -> bool:
        return self._process is not None

    @property
    def interactive(self) -> bool:
        return self.backend in INTERACTIVE_BACKENDS

    def show(self, title: str, message: str) -> None:
        command = self.command(title, message)
        if command is None:
            return
        try:
            self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL, text=True)
        except OSError as error:
            self.error = f"{self.backend} notification failed: {error}"

    def command(self, title: str, message: str) -> list[str] | None:
        if self.backend == "windows popup":
            executable = powershell()
            if executable is None:
                return None
            script = (WINDOWS_POPUP.replace("__TITLE__", ps_quote(title)).replace("__MESSAGE__", ps_quote(message))
                      .replace("__TIMEOUT_MS__", str(self.timeout * 1000)))
            return powershell_command(executable, script)
        if self.backend == "osascript":
            return ["osascript", "-e", MAC_POPUP, title, message, str(self.timeout)]
        if self.backend == "zenity":
            return ["zenity", "--info", f"--title={title}", f"--text={html.escape(message)}", "--ok-label=Stop",
                    "--extra-button=Snooze", f"--timeout={self.timeout}", "--icon-name=alarm-symbolic"]
        if self.backend == "notify-send":
            return ["notify-send", "--urgency=critical", "--app-name=tock", title, message]
        return None

    def poll(self) -> str | None:
        """Return ``dismiss`` or ``snooze`` once the user has pressed a button."""
        if self._process is None or self._process.poll() is None:
            return None
        process, self._process = self._process, None
        output = process.stdout.read() if process.stdout else ""
        return parse_response(self.backend, process.returncode, output)

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None


def parse_response(backend: str, returncode: int, output: str) -> str | None:
    text = output.strip().lower()
    if backend in ("windows popup", "osascript"):
        return text if text in ("dismiss", "snooze") else None
    if backend == "zenity":
        if "snooze" in text:
            return "snooze"
        return "dismiss" if returncode in (0, 1) else None  # 1 is the window's close button; 5 is timeout
    return None
