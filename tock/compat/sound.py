"""Dependency-free generated alarm tone with best-effort native playback."""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

from .windows import powershell, powershell_command, ps_quote, windows_path


def generate_alarm_wav(path: Path) -> Path:
    """Create a short, pleasant WAV beep pattern once, using only the stdlib."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate, duration = 22_050, 0.8
    frames = bytearray()
    for index in range(int(sample_rate * duration)):
        phase = index / sample_rate
        amplitude = 0.38 if (phase % 0.4) < 0.28 else 0.0
        sample = int(32_767 * amplitude * math.sin(2 * math.pi * 880 * phase))
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)
    return path


def selected_backend() -> str:
    if sys.platform == "win32":
        return "winsound"
    if sys.platform == "darwin" and shutil.which("afplay"):
        return "afplay"
    for command in ("paplay", "pw-play", "aplay"):
        if shutil.which(command):
            return command
    if powershell():  # WSL without a Linux audio stack: let Windows play it
        return "powershell"
    return "terminal bell"


class SoundPlayer:
    """Starts a looping sound and guarantees a stop path on every platform."""

    def __init__(self, wav_path: Path, *, stream=None) -> None:
        self.wav_path = wav_path
        self.stream = stream or sys.stdout
        self.backend = selected_backend()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._play, daemon=True, name="alarm-sound")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        if sys.platform == "win32":
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _play(self) -> None:
        if self.backend == "winsound":
            try:
                import winsound
                winsound.PlaySound(str(self.wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
                self._stop.wait()
                return
            except Exception:
                self.backend = "terminal bell"
        while not self._stop.is_set():
            if self.backend == "terminal bell":
                print("\a", end="", flush=True, file=self.stream)
                self._stop.wait(1)
                continue
            command = self._command()
            if command is None:
                self.backend = "terminal bell"
                continue
            try:
                # PowerShell loops the sound itself and exits when its stdin closes, even if we are killed.
                process = subprocess.Popen(command, stdin=subprocess.PIPE if self.backend == "powershell" else subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                self.backend = "terminal bell"
                continue
            self._process = process
            try:
                while process.poll() is None and not self._stop.wait(0.1):
                    pass
                if process.returncode not in (None, 0) and not self._stop.is_set():
                    self.backend = "terminal bell"
            finally:
                if process.poll() is None:
                    process.terminate()
                self._process = None

    def _command(self) -> list[str] | None:
        if self.backend != "powershell":
            return [self.backend, str(self.wav_path)]
        executable, path = powershell(), windows_path(self.wav_path)
        if executable is None or path is None:
            return None
        script = f"$player = New-Object System.Media.SoundPlayer {ps_quote(path)}\n$player.Load()\n$player.PlayLooping()\n[void][Console]::In.ReadLine()"
        return powershell_command(executable, script)
