"""Simple yt-dlp GUI wrapper — download YouTube videos as MP4 or audio (MP3/Opus/AAC)."""

import json
import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUDIO_FORMATS = ("mp3", "opus", "aac")

QUALITY_OPTIONS = {
    "mp4": ["Best", "1080p", "720p", "480p", "360p"],
    "mp3": ["320 kbps", "192 kbps", "128 kbps", "64 kbps"],
    "opus": ["320 kbps", "192 kbps", "128 kbps", "64 kbps"],
    "aac": ["256 kbps", "192 kbps", "128 kbps", "64 kbps"],
}

DEFAULT_OUTPUT_DIR = str(Path.home() / "Downloads")
CONFIG_PATH = Path.home() / ".ytdl_gui_config.json"

YOUTUBE_URL_RE = re.compile(
    r"^https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/.+"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def check_dependencies() -> list[str]:
    """Return a list of missing required executables."""
    missing = []
    for name in ("yt-dlp", "ffmpeg"):
        if shutil.which(name) is None:
            missing.append(name)
    return missing


def is_valid_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_URL_RE.match(url.strip()))


def load_config() -> dict:
    """Load persisted settings, returning defaults on any error."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(data: dict) -> None:
    """Persist settings to disk."""
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass  # non-critical


# ---------------------------------------------------------------------------
# yt-dlp command builder
# ---------------------------------------------------------------------------
def build_command(url: str, fmt: str, quality: str, output_dir: str) -> list[str]:
    """Return the yt-dlp command list for the given settings."""
    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
    cmd = ["yt-dlp", "--no-playlist", "--newline", "--windows-filenames", "-o", output_template]

    if fmt == "mp4":
        if quality == "Best":
            cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]
        else:
            height = quality.replace("p", "")
            cmd += [
                "-f",
                f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
                "--merge-output-format",
                "mp4",
            ]
    else:  # audio: mp3, opus, aac
        bitrate = quality.split()[0]  # e.g. "320"
        cmd += ["-x", "--audio-format", fmt, "--audio-quality", f"{bitrate}k"]

    cmd.append(url)
    return cmd


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("yt-dlp GUI")
        self.resizable(False, False)
        self._proc: subprocess.Popen | None = None
        self._downloading = False
        self._last_error_lines: list[str] = []
        self._config = load_config()
        self._build_ui()
        self._check_deps()

    # -- Dependency check --------------------------------------------------
    def _check_deps(self) -> None:
        missing = check_dependencies()
        if missing:
            names = ", ".join(missing)
            messagebox.showerror(
                "Missing dependencies",
                f"The following required programs were not found on PATH:\n\n"
                f"{names}\n\nPlease install them before using this app.",
            )

    # -- UI ----------------------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}

        # URL
        url_frame = ttk.LabelFrame(self, text="YouTube URL")
        url_frame.pack(fill="x", **pad)

        self.url_var = tk.StringVar()
        ttk.Entry(url_frame, textvariable=self.url_var, width=55).pack(
            side="left", padx=(8, 4), pady=6, fill="x", expand=True
        )
        ttk.Button(url_frame, text="Paste", width=6, command=self._paste_url).pack(
            side="right", padx=(0, 8), pady=6
        )

        # Format + Quality row
        opts_frame = ttk.Frame(self)
        opts_frame.pack(fill="x", **pad)

        # Format
        fmt_frame = ttk.LabelFrame(opts_frame, text="Format")
        fmt_frame.pack(side="left", fill="y", padx=(0, 6))

        saved_fmt = self._config.get("format", "mp4")
        self.fmt_var = tk.StringVar(value=saved_fmt)
        for val in ("mp4", *AUDIO_FORMATS):
            ttk.Radiobutton(
                fmt_frame,
                text=val.upper(),
                value=val,
                variable=self.fmt_var,
                command=self._on_format_change,
            ).pack(side="left", padx=8, pady=6)

        # Quality
        q_frame = ttk.LabelFrame(opts_frame, text="Quality")
        q_frame.pack(side="left", fill="both", expand=True)

        self.quality_var = tk.StringVar()
        self.quality_combo = ttk.Combobox(
            q_frame,
            textvariable=self.quality_var,
            state="readonly",
            width=14,
        )
        self.quality_combo.pack(padx=8, pady=6)
        self._on_format_change()  # populate initial values

        # Restore saved quality if it matches current format
        saved_quality = self._config.get("quality")
        if saved_quality in QUALITY_OPTIONS.get(saved_fmt, []):
            self.quality_var.set(saved_quality)

        # Output folder
        dir_frame = ttk.LabelFrame(self, text="Save to")
        dir_frame.pack(fill="x", **pad)

        saved_dir = self._config.get("output_dir", DEFAULT_OUTPUT_DIR)
        self.dir_var = tk.StringVar(value=saved_dir)
        ttk.Entry(dir_frame, textvariable=self.dir_var, width=48).pack(
            side="left", padx=(8, 4), pady=6, fill="x", expand=True
        )
        ttk.Button(dir_frame, text="Browse", width=7, command=self._browse_dir).pack(
            side="right", padx=(0, 8), pady=6
        )

        # Buttons row
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", **pad)

        self.dl_btn = ttk.Button(
            btn_frame, text="Download", command=self._start_download
        )
        self.dl_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.cancel_btn = ttk.Button(
            btn_frame, text="Cancel", command=self._cancel_download, state="disabled"
        )
        self.cancel_btn.pack(side="left", fill="x", expand=True, padx=(4, 4))

        self.open_btn = ttk.Button(
            btn_frame, text="Open folder", command=self._open_folder, state="disabled"
        )
        self.open_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Progress
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(fill="x", **pad)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=10, pady=(0, 8)
        )

    # -- Callbacks ---------------------------------------------------------
    def _paste_url(self) -> None:
        try:
            self.url_var.set(self.clipboard_get())
        except tk.TclError:
            pass

    def _on_format_change(self) -> None:
        fmt = self.fmt_var.get()
        options = QUALITY_OPTIONS[fmt]
        self.quality_combo["values"] = options
        self.quality_var.set(options[0])

    def _browse_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.dir_var.get())
        if path:
            self.dir_var.set(path)

    def _open_folder(self) -> None:
        folder = self.dir_var.get()
        if os.path.isdir(folder):
            os.startfile(folder)

    # -- Download logic ----------------------------------------------------
    def _start_download(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a YouTube URL.")
            return
        if not is_valid_youtube_url(url):
            messagebox.showwarning(
                "Invalid URL",
                "That doesn't look like a YouTube URL.\n\n"
                "Supported: youtube.com, youtu.be, music.youtube.com",
            )
            return
        if self._downloading:
            return

        # Persist current settings
        save_config(
            {
                "format": self.fmt_var.get(),
                "quality": self.quality_var.get(),
                "output_dir": self.dir_var.get(),
            }
        )

        self._downloading = True
        self._last_error_lines.clear()
        self.dl_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.open_btn.config(state="disabled")
        self.progress_var.set(0)
        self.status_var.set("Starting…")

        cmd = build_command(
            url, self.fmt_var.get(), self.quality_var.get(), self.dir_var.get()
        )
        thread = threading.Thread(target=self._run_download, args=(cmd,), daemon=True)
        thread.start()

    def _cancel_download(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            self.after(0, self.status_var.set, "Cancelled.")
            self.after(0, self._reset_buttons)

    def _run_download(self, cmd: list[str]) -> None:
        """Run yt-dlp in a subprocess and push progress updates to the GUI."""
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in self._proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if not line:
                    continue

                # Keep last few lines for error reporting
                self._last_error_lines.append(line)
                if len(self._last_error_lines) > 10:
                    self._last_error_lines.pop(0)

                # Parse progress: "[download]  45.2% of ~50MiB at 3.5MiB/s ETA 00:08"
                m = re.search(r"(\d+(?:\.\d+)?)%", line)
                if m:
                    self.after(0, self.progress_var.set, float(m.group(1)))
                self.after(0, self.status_var.set, line[:90])

            self._proc.wait()
            if self._proc.returncode == 0:
                self.after(0, self._download_finished, True, "Download complete!")
            else:
                # Build meaningful error from yt-dlp output
                err_lines = [
                    l for l in self._last_error_lines if "ERROR" in l.upper()
                ]
                err_msg = err_lines[-1] if err_lines else "Download failed (unknown error)."
                self.after(0, self._download_finished, False, err_msg)
        except FileNotFoundError:
            self.after(
                0,
                self._download_finished,
                False,
                "yt-dlp not found. Make sure it's installed and on PATH.",
            )
        except Exception as exc:
            self.after(0, self._download_finished, False, str(exc))

    def _download_finished(self, success: bool, msg: str) -> None:
        self._downloading = False
        self._proc = None
        self.progress_var.set(100 if success else 0)
        self.status_var.set(msg)
        self._reset_buttons(success=success)

    def _reset_buttons(self, success: bool = False) -> None:
        self._downloading = False
        self.dl_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.open_btn.config(state="normal" if success else "disabled")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    App().mainloop()
