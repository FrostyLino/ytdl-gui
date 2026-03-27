"""Simple yt-dlp GUI wrapper — download YouTube videos as MP4 or audio (MP3/Opus/AAC)."""

import json
import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

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
# Progress template — yt-dlp outputs this on each tick so we can parse reliably.
_PROGRESS_TPL = "YTDLGUI:%(progress._percent_str)s"


class App(ttk.Window):
    def __init__(self) -> None:
        super().__init__(themename="darkly")
        self.title("yt-dlp GUI")
        self.minsize(500, 0)
        self.resizable(False, False)
        self._proc: subprocess.Popen | None = None
        self._downloading = False
        self._cancelled = False
        self._download_pass = 1  # 1 = first stream, 2 = second stream
        self._last_error_lines: list[str] = []
        self._config = load_config()
        self._input_widgets: list[tk.Widget] = []
        self._build_ui()
        self._center_window()
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
    def _center_window(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self) -> None:
        pad = {"padx": 14, "pady": 6}

        # URL
        url_frame = ttk.LabelFrame(self, text="YouTube URL", bootstyle="info")
        url_frame.pack(fill="x", **pad)

        self.url_var = tk.StringVar()
        url_entry = ttk.Entry(url_frame, textvariable=self.url_var, width=55)
        url_entry.pack(side="left", padx=(10, 4), pady=8, fill="x", expand=True)
        paste_btn = ttk.Button(
            url_frame, text="⎘ Paste", width=8, command=self._paste_url,
            bootstyle="info-outline",
        )
        paste_btn.pack(side="right", padx=(0, 10), pady=8)
        self._input_widgets.extend([url_entry, paste_btn])

        # Format + Quality row
        opts_frame = ttk.Frame(self)
        opts_frame.pack(fill="x", **pad)

        # Format
        fmt_frame = ttk.LabelFrame(opts_frame, text="Format", bootstyle="info")
        fmt_frame.pack(side="left", fill="y", padx=(0, 6))

        saved_fmt = self._config.get("format", "mp4")
        if saved_fmt not in QUALITY_OPTIONS:
            saved_fmt = "mp4"
        self.fmt_var = tk.StringVar(value=saved_fmt)
        for val in ("mp4", *AUDIO_FORMATS):
            rb = ttk.Radiobutton(
                fmt_frame,
                text=val.upper(),
                value=val,
                variable=self.fmt_var,
                command=self._on_format_change,
                bootstyle="info-toolbutton",
            )
            rb.pack(side="left", padx=6, pady=8)
            self._input_widgets.append(rb)

        # Quality
        q_frame = ttk.LabelFrame(opts_frame, text="Quality", bootstyle="info")
        q_frame.pack(side="left", fill="both", expand=True)

        self.quality_var = tk.StringVar()
        self.quality_combo = ttk.Combobox(
            q_frame,
            textvariable=self.quality_var,
            state="readonly",
            width=14,
        )
        self.quality_combo.pack(padx=10, pady=8)
        self._input_widgets.append(self.quality_combo)
        self._on_format_change()  # populate initial values

        # Restore saved quality if it matches current format
        saved_quality = self._config.get("quality")
        if saved_quality in QUALITY_OPTIONS.get(saved_fmt, []):
            self.quality_var.set(saved_quality)

        # Output folder
        dir_frame = ttk.LabelFrame(self, text="Save to", bootstyle="info")
        dir_frame.pack(fill="x", **pad)

        saved_dir = self._config.get("output_dir", DEFAULT_OUTPUT_DIR)
        self.dir_var = tk.StringVar(value=saved_dir)
        dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, width=48)
        dir_entry.pack(side="left", padx=(10, 4), pady=8, fill="x", expand=True)
        browse_btn = ttk.Button(
            dir_frame, text="Browse", width=8, command=self._browse_dir,
            bootstyle="info-outline",
        )
        browse_btn.pack(side="right", padx=(0, 10), pady=8)
        self._input_widgets.extend([dir_entry, browse_btn])

        # Buttons row
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", **pad)

        self.dl_btn = ttk.Button(
            btn_frame, text="▶ Download", command=self._start_download,
            bootstyle="success",
        )
        self.dl_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.cancel_btn = ttk.Button(
            btn_frame, text="✕ Cancel", command=self._cancel_download,
            state="disabled", bootstyle="warning",
        )
        self.cancel_btn.pack(side="left", fill="x", expand=True, padx=(4, 4))

        self.open_btn = ttk.Button(
            btn_frame, text="📂 Open folder", command=self._open_folder,
            state="disabled", bootstyle="secondary",
        )
        self.open_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Separator
        ttk.Separator(self).pack(fill="x", padx=14, pady=(8, 0))

        # Progress
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", **pad)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            prog_frame, variable=self.progress_var, maximum=100,
            bootstyle="success-striped",
        )
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.pct_var = tk.StringVar(value="0 %")
        ttk.Label(prog_frame, textvariable=self.pct_var, width=6, anchor="e").pack(
            side="right"
        )

        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(
            self, textvariable=self.status_var, anchor="w",
            bootstyle="secondary",
        )
        self.status_label.pack(fill="x", padx=14, pady=(0, 12))

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

    # -- UI state helpers ---------------------------------------------------
    def _set_inputs_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for w in self._input_widgets:
            # Combobox uses "readonly" instead of "normal"
            if isinstance(w, ttk.Combobox) and enabled:
                w.config(state="readonly")
            else:
                w.config(state=state)

    # -- Download logic
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

        # Validate output directory
        out_dir = self.dir_var.get()
        if not os.path.isdir(out_dir):
            create = messagebox.askyesno(
                "Folder not found",
                f"The folder does not exist:\n{out_dir}\n\nCreate it?",
            )
            if create:
                try:
                    os.makedirs(out_dir, exist_ok=True)
                except OSError as exc:
                    messagebox.showerror("Error", f"Could not create folder:\n{exc}")
                    return
            else:
                return

        # Persist current settings
        save_config(
            {
                "format": self.fmt_var.get(),
                "quality": self.quality_var.get(),
                "output_dir": out_dir,
            }
        )

        self._downloading = True
        self._cancelled = False
        self._download_pass = 1
        self._last_error_lines.clear()
        self.dl_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.open_btn.config(state="disabled")
        self._set_inputs_enabled(False)
        self._update_progress(0)
        self._set_status("Starting…")

        cmd = build_command(
            url, self.fmt_var.get(), self.quality_var.get(), out_dir
        )
        # Add structured progress template for reliable parsing
        cmd.insert(1, "--progress-template")
        cmd.insert(2, _PROGRESS_TPL)
        thread = threading.Thread(target=self._run_download, args=(cmd,), daemon=True)
        thread.start()

    def _cancel_download(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._cancelled = True
            self._proc.kill()

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
            last_pct = 0.0
            for line in self._proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if not line:
                    continue

                # Keep last few lines for error reporting
                self._last_error_lines.append(line)
                if len(self._last_error_lines) > 10:
                    self._last_error_lines.pop(0)

                # Parse structured progress: "YTDLGUI: 45.2%"
                if line.startswith("YTDLGUI:"):
                    m = re.search(r"(\d+(?:\.\d+)?)%", line)
                    if m:
                        raw_pct = float(m.group(1))
                        # Detect second pass (progress drops significantly)
                        if raw_pct < last_pct - 10:
                            self._download_pass = 2
                        last_pct = raw_pct
                        # Map to overall progress: pass 1 = 0-50%, pass 2 = 50-100%
                        if self._download_pass == 1 and self.fmt_var.get() == "mp4":
                            display_pct = raw_pct * 0.5
                        elif self._download_pass == 2:
                            display_pct = 50 + raw_pct * 0.5
                        else:
                            display_pct = raw_pct  # audio-only = single pass
                        self.after(0, self._update_progress, display_pct)
                else:
                    # Show non-progress status lines (merging, extracting, etc.)
                    self.after(0, self._set_status, line[:90])

            self._proc.wait()

            # If cancelled, just reset — don't show error
            if self._cancelled:
                self.after(0, self._set_status, "Cancelled.", "warning")
                self.after(0, self._reset_ui, False)
                return

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

    def _update_progress(self, pct: float) -> None:
        self.progress_var.set(pct)
        self.pct_var.set(f"{pct:.0f} %")

    def _set_status(self, msg: str, bootstyle: str = "secondary") -> None:
        self.status_var.set(msg)
        self.status_label.config(bootstyle=bootstyle)

    def _download_finished(self, success: bool, msg: str) -> None:
        self._update_progress(100 if success else 0)
        self._set_status(msg, bootstyle="success" if success else "danger")
        if success:
            self.url_var.set("")  # clear URL for next download
        self._reset_ui(success=success)

    def _reset_ui(self, success: bool = False) -> None:
        self._downloading = False
        self._proc = None
        self.dl_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.open_btn.config(state="normal" if success else "disabled")
        self._set_inputs_enabled(True)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    App().mainloop()
