"""Window for changing what Lora is doing on a timer."""

from __future__ import annotations

import threading
import time
from threading import Lock as ThreadLock
import tkinter as tk
from datetime import timedelta
from pathlib import Path
from tkinter import filedialog

import ctypes
from ctypes import wintypes

import simulation
import update_scene
import wand

BANNER_PATH = Path(__file__).resolve().parent / "background.png"
SLIDES_DIR = Path(__file__).resolve().parent / "backgrounds"

BG = "#1c1424"
PANEL = "#2a2033"
TEXT = "#f4eef8"
MUTED = "#b7a8c4"
ACCENT = "#7c4dff"
ACCENT_TEXT = "#ffffff"
ENTRY = "#120d18"
WARN = "#ff8b7b"
KEY = "#010203"
SCENE_LIMIT = 160
# "I will check back every so often": during a wait, the chat is read this often for commands.
MID_CHECK_SECONDS = 10 * 60

DEFAULT_ENVIRONMENT = (
    "outside environment with sunflowers, butterflies, green grass, open field, white Bichon doggy. "
    "Brown bedroom floor, white walls, window to the outside world. Shoerack. Fireplace. "
    "Moon decorations. A chandelier on the ceiling. A bow for the dog on the floor."
)
DEFAULT_BACKSTORY = (
    "Lora is a chatbot who helps Master find things to talk about on stream. "
    "She talks with Master about his day and asks about what he did, where he went, what he ate, "
    "how he felt, what he thought about, what happened to him, and anything else about his daily life. "
    "She asks natural follow-up questions to learn more and discover interesting stories, opinions, "
    "experiences, and random things Master can talk about on stream. After talking with Master, "
    "Lora gives him a simple list of stream topic ideas based on what she learned. "
    "She should only list the topic itself, without explaining or expanding on it. "
    "Lora loves and cares about master."
)


def let_clicks_through(hwnd: int) -> None:
    """Keep the picture window under the controls. An owned window is always forced above its owner, which is the dark-bar glitch."""
    GWL_EXSTYLE = -20
    GWLP_HWNDPARENT = -8
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    user32 = ctypes.windll.user32
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        get_long.restype = ctypes.c_size_t
        set_long.restype = ctypes.c_size_t
        get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
    else:
        get_long = user32.GetWindowLongW
        set_long = user32.SetWindowLongW
    set_long(hwnd, GWLP_HWNDPARENT, 0)
    style = get_long(hwnd, GWL_EXSTYLE) or 0
    set_long(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


def drag_window(hwnd: int) -> None:
    """Hand the current mouse press to Windows as if it were on the title bar."""
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    user32.SetForegroundWindow(wintypes.HWND(hwnd))
    user32.ReleaseCapture()
    send = user32.SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = ctypes.c_ssize_t
    send(hwnd, 0x00A1, 2, ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF))


def slide_files() -> list[Path]:
    files: list[Path] = []
    if SLIDES_DIR.is_dir():
        files = sorted(
            path
            for path in SLIDES_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        )
    if files:
        return files
    if BANNER_PATH.is_file():
        return [BANNER_PATH]
    return []


def add_slides(sources: tuple[str, ...]) -> int:
    SLIDES_DIR.mkdir(exist_ok=True)
    existing = [path for path in slide_files() if path.parent == SLIDES_DIR]
    if not existing and BANNER_PATH.is_file():
        save_banner(str(BANNER_PATH), SLIDES_DIR / "001.png")
    number = 1
    taken = {path.name for path in SLIDES_DIR.iterdir()}
    while f"{number:03d}.png" in taken:
        number += 1
    count = 0
    for source in sources:
        save_banner(source, SLIDES_DIR / f"{number:03d}.png")
        number += 1
        count += 1
    return count


def clear_slides() -> None:
    if SLIDES_DIR.is_dir():
        for path in SLIDES_DIR.iterdir():
            if path.is_file():
                path.unlink()
    BANNER_PATH.unlink(missing_ok=True)


def save_banner(source: str, dest: Path) -> None:
    from PIL import Image

    image = Image.open(source).convert("RGB")
    image.thumbnail((1600, 1600))
    image.save(dest, "PNG")


def fit_image(path: Path, width: int, height: int):
    """Show the whole picture inside the window, with a dimmed copy filling the edges."""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    backdrop = Image.blend(cover_image(path, width, height), Image.new("RGB", (width, height), (8, 6, 12)), 0.55)
    scale = min(width / image.width, height / image.height)
    fitted = image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    backdrop.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return backdrop


def cover_image(path: Path, width: int, height: int):
    from PIL import Image

    image = Image.open(path).convert("RGB")
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Lora")
        self.configure(bg=BG)
        self.minsize(480, 340)
        self.geometry("560x400")
        self.phase = "idle"
        self.stop_requested = False
        self.clock_id = None
        self.deadline = 0.0
        self.stopped_on = ""
        prefs = update_scene.load_prefs()
        self.last_greet_date = str(prefs.get("last_greet_date", ""))
        try:
            self.opacity_value = int(str(prefs.get("opacity", "100")))
        except ValueError:
            self.opacity_value = 100
        self.opacity_value = max(0, min(100, self.opacity_value))
        self._opacity_save = None
        try:
            self.slide_seconds = int(str(prefs.get("slide_seconds", "15")))
        except ValueError:
            self.slide_seconds = 15
        self.slide_seconds = max(1, min(self.slide_seconds, 3600))
        self.slide_index = 0
        self.next_slide = time.monotonic() + self.slide_seconds
        self.plate = None
        self.plate_label = None
        self._plate_photo = None
        self._plate_spot = None
        self._glass = False
        self._solid: dict[tk.Misc, dict[str, str]] = {}
        self._bases: dict[tuple[str, int, int], object] = {}
        self._glass_sig = None
        self._glass_job = None

        self.columnconfigure(0, weight=1)
        self.note_until = 0.0
        self.narrator_peeked = False
        self.narrator_peeking = False
        self.narrator_request = ""
        self.narrator_speaker = ""
        self.narrator_extend_at = 0
        self.narrator_after = 0
        self.command_stamp = 0
        self.narrator_kind = ""
        self.next_mid_check = float("inf")
        self.next_world_refresh = 0.0
        self.wand_id = None
        self.wand_gate = ThreadLock()

        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        header.columnconfigure(0, weight=1)
        self._text(header, text="Lora", fg=TEXT, font=("Segoe UI", 22, "bold")).grid(row=0, column=0, sticky="w")
        self.clock_label = self._text(header, text="", fg=MUTED, font=("Segoe UI", 10))
        self.clock_label.grid(row=0, column=1, sticky="e")
        self.bind("<Configure>", self._on_configure)

        setting_header = tk.Frame(self, bg=BG)
        setting_header.grid(row=1, column=0, sticky="ew", padx=24)
        setting_header.columnconfigure(0, weight=1)
        self._text(setting_header, text="Current setting", fg=MUTED, font=("Segoe UI", 10)).grid(
            row=0, column=0, sticky="w"
        )
        self.count_label = self._text(setting_header, text=f"0/{SCENE_LIMIT}", fg=MUTED, font=("Segoe UI", 10))
        self.count_label.grid(row=0, column=1, sticky="e")

        self.prior = tk.Text(
            self,
            height=2,
            wrap="word",
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 11),
            padx=10,
            pady=8,
        )
        self.prior.grid(row=2, column=0, sticky="ew", padx=24, pady=(6, 8))
        self.prior.insert("1.0", update_scene.saved_prior())
        self.prior.bind("<KeyRelease>", lambda _event: self.refresh_count())
        self.refresh_count()

        actions = tk.Frame(self, bg=BG)
        actions.grid(row=3, column=0, sticky="ew", padx=24, pady=(8, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        self.ask_button = self._button(actions, "Change action", self.start, ACCENT, ACCENT_TEXT, 0)
        self.stop_button = self._button(actions, "Stop", self.stop, PANEL, TEXT, 1, disabled=True)
        self.idle_background = self._button(
            actions, "Pictures", lambda: self.toggle_drop("Pictures"), PANEL, TEXT, 1
        )
        self.idle_background.grid_remove()

        self.countdown = self._text(self, text="Waiting to start.", fg=MUTED, font=("Segoe UI", 11), justify="left")
        self.countdown.grid(row=4, column=0, sticky="w", padx=24, pady=(12, 0))
        self.status = self._text(self, text="", fg=MUTED, font=("Segoe UI", 9), justify="left")
        self.status.grid(row=5, column=0, sticky="w", padx=24, pady=(2, 0))

        # Six equal buttons, three per row. Each opens one panel under them.
        drops = tk.Frame(self, bg=BG)
        drops.grid(row=6, column=0, sticky="ew", padx=24, pady=(14, 16))
        for column in range(3):
            drops.columnconfigure(column, weight=1, uniform="drop")
        self.drop_open = ""
        self.drop_buttons: dict[str, tk.Button] = {}
        self.drop_panels: dict[str, tk.Frame] = {}
        for index, name in enumerate(("Schedule", "Mochi", "World", "Environment", "Backstory", "Pictures")):
            self.drop_buttons[name] = self._drop_button(drops, name, index // 3, index % 3)

        schedule = self._panel(columns=(1,))
        self.minutes = self._labeled_entry(schedule, 0, 0, "Every", str(prefs.get("minutes", "10")), 8)
        self._hint(schedule, "minutes, or 2h for hours", 0)
        self.times = self._labeled_entry(schedule, 1, 0, "At", str(prefs.get("times", "")), 22)
        self._hint(schedule, "like 8:00 AM, noon, night", 1)
        self.wake = self._labeled_entry(schedule, 2, 0, "Wake at", self._clock_text(prefs.get("wake"), "8:00 AM"), 10)
        self._hint(schedule, "she starts her day", 2)
        self.ai_minutes = tk.BooleanVar(value=str(prefs.get("ai_minutes", "1")) != "0")
        self._check(schedule, "DeepSeek decides how long each action lasts", self.ai_minutes, 3)
        self.heads_up = tk.BooleanVar(value=str(prefs.get("heads_up", "1")) != "0")
        self._check(
            schedule,
            "Narrator gives a 1-minute heads-up before each change",
            self.heads_up,
            4,
            command=self._save_heads_up,
        )
        self.drop_panels["Schedule"] = schedule

        mochi = self._panel(columns=(1,))
        self.wand_on = tk.BooleanVar(value=str(prefs.get("wand", "0")) == "1")
        self._check(mochi, "Mochi taps the wand", self.wand_on, 0, command=self._toggle_wand)
        self.wand_minutes = self._labeled_entry(mochi, 1, 0, "Talk every", str(prefs.get("wand_minutes", "10")), 8)
        self._hint(mochi, "minutes. Kindroid writes the line, no browser.", 1)
        self.drop_panels["Mochi"] = mochi

        environment = self._panel(columns=(0,))
        self.environment = self._text_box(environment, str(prefs.get("environment") or DEFAULT_ENVIRONMENT))
        self.drop_panels["Environment"] = environment

        backstory = self._panel(columns=(0,))
        self.backstory = self._text_box(backstory, str(prefs.get("backstory") or DEFAULT_BACKSTORY))
        self.drop_panels["Backstory"] = backstory

        pictures = self._panel()
        self.bg_button = self._small_button(pictures, "Add pictures", self.choose_background)
        self.bg_button.grid(row=0, column=0, sticky="w")
        self.clear_bg = self._small_button(pictures, "Clear", self.clear_background)
        self.clear_bg.grid(row=0, column=1, sticky="w", padx=(6, 16))
        self._text(pictures, text="Visible", fg=MUTED, font=("Segoe UI", 9)).grid(row=0, column=2, sticky="e", padx=(12, 0))
        self.opacity = tk.Scale(
            pictures,
            from_=0,
            to=100,
            orient="horizontal",
            showvalue=False,
            bg=PANEL,
            fg=TEXT,
            troughcolor=ENTRY,
            activebackground=ACCENT,
            highlightthickness=0,
            bd=0,
            sliderrelief="flat",
            length=90,
            command=self._on_opacity,
        )
        self.opacity.grid(row=0, column=3, sticky="w", padx=(6, 4))
        self.opacity_label = self._text(pictures, text=f"{self.opacity_value}%", fg=TEXT, font=("Segoe UI", 9), width=4, anchor="w")
        self.opacity_label.grid(row=0, column=4, sticky="w", padx=(0, 12))
        self._text(pictures, text="Seconds", fg=MUTED, font=("Segoe UI", 9)).grid(row=0, column=5, sticky="e")
        self.slide_every = tk.Entry(
            pictures,
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            width=4,
            justify="right",
        )
        self.slide_every.insert(0, str(self.slide_seconds))
        self.slide_every.grid(row=0, column=6, sticky="w", padx=(6, 0))
        self.slide_every.bind("<FocusOut>", self._save_slide_seconds)
        self.slide_every.bind("<Return>", self._save_slide_seconds)
        if not slide_files():
            self.clear_bg.grid_remove()
        self.drop_panels["Pictures"] = pictures

        world = self._panel(columns=(1,))
        self.world_rows: dict[str, tk.Label] = {}
        for index, label in enumerate(
            ("Place", "Doing", "Mochi", "Time", "Weather", "Goal", "Last event", "Memory", "Discovered")
        ):
            self._text(world, text=label, fg=MUTED, font=("Segoe UI", 9)).grid(
                row=index, column=0, sticky="nw", padx=(0, 10), pady=1
            )
            value = self._text(world, text="", fg=TEXT, font=("Segoe UI", 9), wraplength=400, justify="left")
            value.grid(row=index, column=1, sticky="w", pady=1)
            self.world_rows[label] = value
        self.drop_panels["World"] = world
        self._wrap_text(560)
        for panel in self.drop_panels.values():
            panel.grid_remove()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Activate>", self._keep_plate_behind)
        self.bind("<Map>", self._on_map)
        self.bind("<Unmap>", self._on_unmap)
        self.opacity.set(self.opacity_value)
        self._chrome = [
            header,
            setting_header,
            self.prior,
            self.countdown,
            self.status,
            drops,
            self.stop_button,
        ]
        self._compact = False
        self._show_compact(True)
        self.clock()
        self.after(100, self._sync_glass)
        self.after(400, self._resume_schedule)

    def _clock_text(self, value: str | None, default: str) -> str:
        if not value:
            return default
        try:
            return update_scene.format_clock(update_scene.parse_clock(value))
        except RuntimeError:
            return default

    def _text(self, parent: tk.Misc, **kwargs) -> tk.Label:
        kwargs.setdefault("bg", BG)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("padx", 0)
        kwargs.setdefault("pady", 0)
        kwargs.setdefault("highlightthickness", 0)
        return tk.Label(parent, **kwargs)

    def _labeled_entry(self, parent: tk.Frame, row: int, column: int, label: str, value: str, width: int) -> tk.Entry:
        self._text(parent, text=label, fg=MUTED, font=("Segoe UI", 10)).grid(
            row=row, column=column, sticky="w", padx=(0, 6), pady=4
        )
        entry = tk.Entry(
            parent,
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 11),
            width=width,
        )
        entry.insert(0, value)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=4)
        return entry

    def _drop_button(self, parent: tk.Frame, name: str, row: int, column: int) -> tk.Button:
        button = tk.Button(
            parent,
            text=name,
            command=lambda: self.toggle_drop(name),
            bg=PANEL,
            fg=TEXT,
            activebackground="#3a3144",
            activeforeground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            padx=8,
            pady=6,
            cursor="hand2",
        )
        button.grid(row=row, column=column, sticky="ew", padx=(0, 0 if column == 2 else 8), pady=(0 if row == 0 else 8, 0))
        return button

    def _panel(self, columns: tuple[int, ...] = ()) -> tk.Frame:
        """A panel that opens under the six buttons. All panels share one spot."""
        panel = tk.Frame(self, bg=BG)
        panel.grid(row=8, column=0, sticky="ew", padx=24, pady=(0, 16))
        for column in columns:
            panel.columnconfigure(column, weight=1)
        return panel

    def _hint(self, parent: tk.Frame, text: str, row: int) -> None:
        self._text(parent, text=text, fg=MUTED, font=("Segoe UI", 9)).grid(row=row, column=2, sticky="w")

    def _check(self, parent: tk.Frame, text: str, variable: tk.BooleanVar, row: int, command=None) -> None:
        tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=command,
            bg=BG,
            fg=TEXT,
            selectcolor=ENTRY,
            activebackground=BG,
            activeforeground=TEXT,
            relief="flat",
            highlightthickness=0,
            bd=0,
            font=("Segoe UI", 9),
            padx=0,
            pady=2,
            cursor="hand2",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _small_button(self, parent: tk.Frame, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=PANEL,
            fg=TEXT,
            activebackground="#3a3144",
            activeforeground=TEXT,
            relief="flat",
            font=("Segoe UI", 9),
            padx=8,
            pady=2,
            cursor="hand2",
        )

    def _text_box(self, parent: tk.Frame, value: str) -> tk.Text:
        box = tk.Text(
            parent,
            height=4,
            wrap="word",
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            padx=10,
            pady=8,
        )
        box.grid(row=0, column=0, sticky="ew")
        box.insert("1.0", value)
        return box

    def _button(
        self,
        parent: tk.Frame,
        text: str,
        command,
        bg: str,
        fg: str,
        column: int,
        disabled: bool = False,
    ) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground="#6a3df0" if bg == ACCENT else "#3a3144",
            activeforeground=fg,
            disabledforeground="#9a8aa8",
            relief="flat",
            font=("Segoe UI", 12, "bold" if bg == ACCENT else "normal"),
            padx=12,
            pady=8,
            state="disabled" if disabled else "normal",
            cursor="hand2",
        )
        button.grid(row=0, column=column, sticky="ew", padx=(0, 8) if column < 2 else 0)
        return button

    def refresh_count(self) -> None:
        count = len(self.prior.get("1.0", "end-1c"))
        self.count_label.configure(text=f"{count}/{SCENE_LIMIT}", fg=WARN if count > SCENE_LIMIT else MUTED)

    def _show_compact(self, compact: bool) -> None:
        """Before the simulation starts, the picture fills the window. Change action and Pictures stay up."""
        if compact == self._compact:
            return
        self._compact = compact
        if compact and self.drop_open and self.drop_open != "Pictures":
            self.toggle_drop(self.drop_open)
        for widget in self._chrome:
            widget.grid_remove() if compact else widget.grid()
        if compact:
            self.idle_background.grid(row=0, column=1, sticky="ew")
        else:
            self.idle_background.grid_remove()
        self._glass_sig = None

    def _label_rects(self) -> list[tuple[int, int, int, int]]:
        origin_x = self.winfo_rootx()
        origin_y = self.winfo_rooty()
        rects = []
        for widget in self._each_widget():
            if widget.winfo_class() != "Label" or not widget.winfo_ismapped():
                continue
            if not str(widget.cget("text")).strip():
                continue
            x = widget.winfo_rootx() - origin_x
            y = widget.winfo_rooty() - origin_y
            rects.append((x - 3, y - 1, x + widget.winfo_width() + 3, y + widget.winfo_height() + 1))
        return rects

    def clock(self) -> None:
        try:
            now = update_scene.la_now()
            label = now.strftime("%I:%M %p").lstrip("0")
            self.clock_label.configure(text=f"{label} Los Angeles")
            if self.phase == "waiting":
                self.follow_schedule(now)
            elif self.phase == "idle":
                self.maybe_autostart(now)
            if time.monotonic() >= self.next_world_refresh:
                self.refresh_world()
            self._advance_slide()
            self._sync_glass()
        except Exception as caught:
            self.log(f"Could not update the window. {caught}")
        self.clock_id = self.after(1000, self.clock)

    def _on_opacity(self, value: str) -> None:
        opacity = max(0, min(100, int(float(value))))
        if opacity == self.opacity_value:
            return
        self.opacity_value = opacity
        self.opacity_label.configure(text=f"{opacity}%")
        self._queue_glass()
        if self._opacity_save is not None:
            self.after_cancel(self._opacity_save)
        self._opacity_save = self.after(300, self._save_opacity)

    def _save_opacity(self) -> None:
        self._opacity_save = None
        prefs = update_scene.load_prefs()
        prefs["opacity"] = str(self.opacity_value)
        update_scene.save_prefs(prefs)

    def _save_slide_seconds(self, _event=None) -> None:
        try:
            value = int(self.slide_every.get().strip())
        except ValueError:
            value = self.slide_seconds
        value = max(1, min(value, 3600))
        self.slide_seconds = value
        if self.slide_every.get().strip() != str(value):
            self.slide_every.delete(0, "end")
            self.slide_every.insert(0, str(value))
        self.next_slide = time.monotonic() + value
        prefs = update_scene.load_prefs()
        prefs["slide_seconds"] = str(value)
        update_scene.save_prefs(prefs)

    def _current_slide(self) -> Path | None:
        files = slide_files()
        if not files:
            return None
        self.slide_index %= len(files)
        return files[self.slide_index]

    def _advance_slide(self) -> None:
        if time.monotonic() < self.next_slide:
            return
        self.next_slide = time.monotonic() + self.slide_seconds
        files = slide_files()
        if len(files) < 2:
            return
        self.slide_index = (self.slide_index + 1) % len(files)

    def _queue_glass(self) -> None:
        if self._glass_job is None:
            self._glass_job = self.after(15, self._run_glass_job)

    def _run_glass_job(self) -> None:
        self._glass_job = None
        self._sync_glass()

    def _sync_glass(self) -> None:
        path = self._current_slide()
        if path is None:
            if self._glass:
                self._leave_glass()
            return
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 20 or height < 20 or self.state() == "iconic":
            return
        if not self._glass:
            self._enter_glass()
        self.update_idletasks()
        rects = tuple(self._label_rects())
        sig = (str(path), width, height, self.opacity_value, rects)
        if sig != self._glass_sig:
            self._glass_sig = sig
            try:
                self._draw_plate(path, width, height, rects)
            except Exception as caught:
                self.log(f"Could not show the background. {caught}")
        self._place_plate()

    def _enter_glass(self) -> None:
        self._ensure_plate()
        self._solid = {}
        for widget in self._each_widget():
            kind = widget.winfo_class()
            if kind in ("Tk", "Frame", "Label"):
                keys = ("bg",)
            else:
                continue
            self._solid[widget] = {key: str(widget.cget(key)) for key in keys}
            widget.configure(**{key: KEY for key in keys})
        self.attributes("-transparentcolor", KEY)
        self.plate.deiconify()
        self.plate.update_idletasks()
        self._glass = True
        self._glass_sig = None
        self._plate_spot = None

    def _leave_glass(self) -> None:
        self.attributes("-transparentcolor", "")
        for widget, colors in self._solid.items():
            if widget.winfo_exists():
                widget.configure(**colors)
        self._solid = {}
        self._bases = {}
        if self.plate is not None:
            self.plate.withdraw()
        self._glass = False
        self._glass_sig = None

    def _rgb(self, color: str) -> tuple[int, int, int]:
        red, green, blue = self.winfo_rgb(color)
        return (red >> 8, green >> 8, blue >> 8)

    def _draw_plate(self, path: Path, width: int, height: int, rects=()) -> None:
        from PIL import Image, ImageTk

        key = (str(path), width, height)
        base = self._bases.get(key)
        if base is None:
            if len(self._bases) > 20 or any(size[1:] != (width, height) for size in self._bases):
                self._bases = {}
            base = fit_image(path, width, height)
            self._bases[key] = base
        level = self.opacity_value / 100
        image = Image.blend(base, Image.new("RGB", (width, height), self._rgb(BG)), level)
        shade = Image.new("RGB", (1, 1), (8, 6, 14))
        for left, top, right, bottom in rects:
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)
            if right <= left or bottom <= top:
                continue
            patch = image.crop((left, top, right, bottom))
            tint = shade.resize(patch.size)
            image.paste(Image.blend(patch, tint, 0.62), (left, top))
        photo = self._plate_photo
        if photo is not None and (photo.width(), photo.height()) == (width, height):
            photo.paste(image)
        else:
            self._plate_photo = ImageTk.PhotoImage(image)
            self.plate_label.configure(image=self._plate_photo)

    def _keep_plate_behind(self, _event=None) -> None:
        if self._glass:
            self._place_plate(force=True)

    def _on_map(self, event) -> None:
        if event.widget is self and self._glass and self.plate is not None:
            self.plate.deiconify()
            self.plate.update_idletasks()
            self._place_plate(force=True)

    def _on_unmap(self, event) -> None:
        if event.widget is self and self.plate is not None:
            self.plate.withdraw()

    def _close(self) -> None:
        self.cancel_wand()
        if self.plate is not None:
            self.plate.destroy()
            self.plate = None
        self.destroy()

    def _each_widget(self):
        stack = [self]
        while stack:
            widget = stack.pop()
            if widget is self.opacity or widget is self.plate:
                continue
            yield widget
            stack.extend(widget.winfo_children())

    def _ensure_plate(self) -> None:
        if self.plate is not None:
            return
        self.plate = tk.Toplevel(self)
        self.plate.withdraw()
        self.plate.overrideredirect(True)
        self.plate_label = tk.Label(self.plate, bd=0, highlightthickness=0, bg=KEY)
        self.plate_label.pack(fill="both", expand=True)
        self.plate_label.bind("<ButtonPress-1>", self._grab_from_picture)
        self.plate.update_idletasks()
        let_clicks_through(ctypes.windll.user32.GetAncestor(self.plate.winfo_id(), 2))

    def _grab_from_picture(self, _event=None) -> None:
        """The picture window never activates, so a click on it brings the window forward and drags it."""
        drag_window(ctypes.windll.user32.GetAncestor(self.winfo_id(), 2))

    def _place_plate(self, force: bool = False) -> None:
        if self.plate is None or not self._glass:
            return
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 20 or height < 20:
            return
        x = self.winfo_rootx()
        y = self.winfo_rooty()
        spot = (x, y, width, height)
        moved = spot != self._plate_spot or force
        self._plate_spot = spot
        if moved:
            self.plate.geometry(f"{width}x{height}+{x}+{y}")
        user32 = ctypes.windll.user32
        plate_hwnd = user32.GetAncestor(self.plate.winfo_id(), 2)
        main_hwnd = user32.GetAncestor(self.winfo_id(), 2)
        let_clicks_through(plate_hwnd)
        flags = 0x0010
        if not moved:
            flags |= 0x0001 | 0x0002
        user32.SetWindowPos(plate_hwnd, main_hwnd, x, y, width, height, flags)

    def maybe_autostart(self, now) -> None:
        if not simulation.schedule()["running"]:
            return
        try:
            options = self.read_options()
        except RuntimeError:
            return
        if not update_scene.should_autostart(
            now,
            options["wake"],
            options["quiet_from"],
            options["quiet_to"],
            self.last_greet_date,
            self.stopped_on,
        ):
            if (
                time.monotonic() >= self.note_until
                and self.stopped_on != now.date().isoformat()
                and self.last_greet_date != now.date().isoformat()
            ):
                wake = update_scene.format_clock(options["wake"])
                self.countdown.configure(text=f"Starts at {wake}.")
            return
        self.store_prefs(options)
        self.last_greet_date = now.date().isoformat()
        self._save_greet_date()
        wake = options["wake_text"]
        self.log(f"Morning. Setting what she is doing for {wake}.")
        self.shift_setting(options, morning=True)

    def log(self, text: str) -> None:
        message = " ".join(text.split())
        simulation.log_event(message)
        lowered = message.lower()
        if (
            lowered.startswith("could not")
            or lowered.startswith("minutes")
            or lowered.startswith("every")
            or lowered.startswith("use times")
            or lowered.startswith("set every")
            or "something went wrong" in lowered
        ):
            self.note(message)

    def _say(self, message: str) -> None:
        self.log(message)
        self.note(message)

    def note(self, message: str) -> None:
        self.note_until = time.monotonic() + 8
        self.countdown.configure(text=message)

    def refresh_world(self) -> None:
        self.next_world_refresh = time.monotonic() + 30
        try:
            info = simulation.summary(self.environment.get("1.0", "end").strip())
        except Exception as caught:
            self.status.configure(text=f"World could not be read. {caught}")
            return
        if self.phase in ("waiting", "busy"):
            state = "Running"
        elif not info["running"]:
            state = "Stopped"
        else:
            state = "Idle"
        parts = [state]
        if info.get("last_at"):
            parts.append(f"last {simulation.clock_text(info['last_at'])}")
        if self.phase == "waiting":
            upcoming = update_scene.la_now() + timedelta(seconds=max(0, self.deadline - time.monotonic()))
            parts.append(f"next {simulation.clock_text(upcoming)}")
        if info.get("pending"):
            parts.append("Kindroid update pending")
        self.status.configure(text=" · ".join(parts))
        if not info.get("ready"):
            for value in self.world_rows.values():
                value.configure(text="")
            self.world_rows["Place"].configure(text="Starts on the first change.")
            return
        goal = info["goal"] + (f" · {info['progress']}" if info["progress"] else "") if info["goal"] else "none"
        rows = {
            "Place": info["place"],
            "Doing": info["activity"] + (f" · about {info['minutes']} min" if info.get("minutes") else ""),
            "Mochi": info["mochi"],
            "Time": info["time"],
            "Weather": f"{info['weather']} · {info['lighting']}".strip(" ·"),
            "Goal": goal,
            "Last event": info["event"],
            "Memory": "\n".join(f"- {text}" for text in info["memories"]) or "none yet",
            "Discovered": ", ".join(info["discovered"][:5]) or "none yet",
        }
        for label, text in rows.items():
            self.world_rows[label].configure(text=text)

    def toggle_drop(self, name: str) -> None:
        if self.drop_open == name:
            self.drop_panels[name].grid_remove()
            self._mark_drop(name, False)
            self.drop_open = ""
        else:
            if self.drop_open:
                self.drop_panels[self.drop_open].grid_remove()
                self._mark_drop(self.drop_open, False)
            if name == "World":
                self.refresh_world()
            self.drop_panels[name].grid()
            self._mark_drop(name, True)
            self.drop_open = name
            self.update_idletasks()
            needed = self.winfo_reqheight()
            if self.winfo_height() < needed:
                self.geometry(f"{max(self.winfo_width(), 560)}x{needed}")
        self._queue_glass()

    def _mark_drop(self, name: str, open_: bool) -> None:
        buttons = [self.drop_buttons[name]]
        if name == "Pictures":
            buttons.append(self.idle_background)
        for button in buttons:
            if open_:
                button.configure(bg=ACCENT, fg=ACCENT_TEXT, activebackground="#6a3df0", activeforeground=ACCENT_TEXT)
            else:
                button.configure(bg=PANEL, fg=TEXT, activebackground="#3a3144", activeforeground=TEXT)

    def set_setting(self, scene: str) -> None:
        self.prior.configure(state="normal")
        self.prior.delete("1.0", "end")
        self.prior.insert("1.0", scene)
        self.refresh_count()

    def read_options(self) -> dict:
        minutes = simulation.parse_every(self.minutes.get())
        times = simulation.parse_times(self.times.get())
        if minutes < 1 and not times:
            raise RuntimeError("Set Every, or add a time under At.")
        saved = update_scene.load_prefs()
        quiet_from = update_scene.parse_clock(str(saved.get("quiet_from") or "10:00 PM"))
        quiet_to = update_scene.parse_clock(str(saved.get("quiet_to") or "8:00 AM"))
        wake = update_scene.parse_clock(self.wake.get())
        return {
            "minutes": minutes,
            "times": times,
            "every_text": self.minutes.get().strip(),
            "times_text": self.times.get().strip(),
            "tone": str(saved.get("tone") or "robotic tone"),
            "quiet_from": quiet_from,
            "quiet_to": quiet_to,
            "wake": wake,
            "quiet_from_text": str(saved.get("quiet_from") or "10:00 PM"),
            "quiet_to_text": str(saved.get("quiet_to") or "8:00 AM"),
            "wake_text": self.wake.get().strip(),
            "ai_minutes": bool(self.ai_minutes.get()),
        }

    def store_prefs(self, options: dict) -> None:
        prefs = update_scene.load_prefs()
        prefs.update(
            {
                "minutes": options["every_text"] or str(options["minutes"]),
                "times": options["times_text"],
                "ai_minutes": "1" if options.get("ai_minutes") else "0",
                "heads_up": "1" if self.heads_up.get() else "0",
                "wand": "1" if self.wand_on.get() else "0",
                "wand_minutes": self.wand_minutes.get().strip(),
                "tone": options["tone"],
                "quiet_from": options["quiet_from_text"],
                "quiet_to": options["quiet_to_text"],
                "wake": options["wake_text"],
                "opacity": str(self.opacity_value),
                "slide_seconds": str(self.slide_seconds),
                "last_greet_date": self.last_greet_date,
                "environment": self.environment.get("1.0", "end").strip(),
                "backstory": self.backstory.get("1.0", "end").strip(),
            }
        )
        update_scene.save_prefs(prefs)

    def _on_configure(self, event) -> None:
        if event.widget is not self:
            return
        self._wrap_text(event.width)
        if not self._glass:
            return
        self._place_plate()
        self._queue_glass()

    def _wrap_text(self, width: int) -> None:
        """Long messages wrap onto more lines instead of running off the edges."""
        room = max(200, width - 48)
        if room == getattr(self, "_wrap_room", None):
            return
        self._wrap_room = room
        self.countdown.configure(wraplength=room)
        self.status.configure(wraplength=room)
        for value in self.world_rows.values():
            value.configure(wraplength=max(150, room - 90))

    def choose_background(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Add pictures",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif"), ("All files", "*.*")],
        )
        if not paths:
            return
        try:
            added = add_slides(paths)
        except Exception as caught:
            self.log(f"Could not use those pictures. {caught}")
            return
        files = slide_files()
        self.slide_index = max(0, len(files) - added)
        self.next_slide = time.monotonic() + self.slide_seconds
        self.clear_bg.grid()
        self._sync_glass()
        word = "picture" if added == 1 else "pictures"
        self.log(f"Added {added} {word}. {len(files)} in the slideshow, changing every {self.slide_seconds} seconds.")

    def clear_background(self) -> None:
        clear_slides()
        self.slide_index = 0
        self._sync_glass()
        self.clear_bg.grid_remove()

    def start(self) -> None:
        if self.phase != "idle":
            return
        try:
            options = self.read_options()
        except RuntimeError as error:
            self._show_compact(False)
            self.log(str(error))
            return
        self.stopped_on = ""
        self.store_prefs(options)
        self.stop_requested = False
        simulation.set_schedule(True)
        if self.heads_up.get():
            # Heads-up first, then the change a minute later, following whatever she asks for.
            self._show_compact(False)
            self.arm_timer(options, delay=update_scene.HEADS_UP_SECONDS)
            self.start_heads_up = True
            self.log("Started. Narrator gives her the heads-up now, and the change comes in 1 minute.")
            self.note("Heads-up now. The change comes in 1 minute.")
            self.arm_wand()
            return
        self.shift_setting(options)
        self.arm_wand()

    def _resume_schedule(self) -> None:
        """After a restart, pick the timer back up where the simulation left it."""
        if self.phase != "idle":
            return
        plan = simulation.schedule()
        if not plan["running"] or plan["next_at"] is None:
            self.refresh_world()
            return
        try:
            options = self.read_options()
        except RuntimeError as error:
            self.log(str(error))
            return
        wait = (plan["next_at"] - update_scene.la_now()).total_seconds()
        if wait < 60:
            wait = 60
            self.log("The next change was due while the app was closed. It runs in a minute.")
        self._show_compact(False)
        self.arm_timer(options, delay=wait)
        self.arm_wand()
        self.log(f"Resumed. Next change at {simulation.clock_text(update_scene.la_now() + timedelta(seconds=wait))}.")

    def _save_greet_date(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["last_greet_date"] = self.last_greet_date
        update_scene.save_prefs(prefs)

    def _arm_after_turn(self, options: dict, minutes: int | None = None, delay: float | None = None) -> None:
        self.arm_timer(options, minutes=minutes, delay=delay)

    def arm_timer(
        self,
        options: dict,
        hold_for_quiet: bool = False,
        delay: float | None = None,
        minutes: int | None = None,
    ) -> None:
        now = update_scene.la_now()
        if delay is None:
            delay = simulation.next_delay(
                now, options["minutes"], options.get("times") or [], bool(options.get("ai_minutes")), minutes
            )
        self.phase = "waiting"
        self.pending = options
        self.deadline = time.monotonic() + delay
        self.narrator_peeked = False
        self.narrator_peeking = False
        self.narrator_request = ""
        self.narrator_speaker = ""
        self.narrator_extend_at = 0
        # Read from the last change or extension, whichever is newer, so old commands are not read again.
        start = max(simulation.last_step_ms() or int(time.time() * 1000), simulation.last_extend_ms())
        self.narrator_after = start - 60_000
        self.command_stamp = 0
        self.narrator_kind = ""
        self.next_mid_check = time.monotonic() + MID_CHECK_SECONDS
        self.warned = False
        self.start_heads_up = False
        self.heads_up_busy = False
        self.wait_total = delay
        simulation.set_schedule(True, now + timedelta(seconds=delay))
        if time.monotonic() >= self.note_until:
            self.countdown.configure(text=self._next_text(int(delay), now))
        self.ask_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.refresh_world()

    def _next_text(self, remaining: int, now) -> str:
        if remaining >= 3600:
            at = now + timedelta(seconds=remaining)
            return f"Next change at {simulation.clock_text(at)}"
        minutes, seconds = divmod(max(0, remaining), 60)
        return f"Next change in {minutes}:{seconds:02d}"

    def follow_schedule(self, now) -> None:
        options = getattr(self, "pending", None)
        if not options:
            return
        wake_now = update_scene.clock_minutes(now) == options["wake"]
        if wake_now and self.last_greet_date != now.date().isoformat():
            self.last_greet_date = now.date().isoformat()
            self._save_greet_date()
            self.log(f"Morning. Setting what she is doing for {options['wake_text']}.")
            self.shift_setting(options, morning=True)
            return
        remaining = int(self.deadline - time.monotonic())
        self._maybe_mid_check(remaining)
        self._maybe_heads_up(remaining)
        # With the heads-up, the chat is read after her answer to it, 30 seconds before the change.
        peek_at = 30 if self._heads_up_on() else 60
        busy = getattr(self, "heads_up_busy", False)
        if remaining <= peek_at and not self.narrator_peeked and not self.narrator_peeking and not busy:
            self.narrator_peeked = True
            self.narrator_peeking = True
            threading.Thread(target=self._peek_narrator, daemon=True).start()
        if remaining <= 0:
            if self.narrator_peeking or busy or not self.narrator_peeked:
                return
            self.shift_setting(
                options,
                request=self.narrator_request,
                requested_by=self.narrator_speaker,
                checked=self.narrator_peeked,
                extend_at=self.narrator_extend_at,
                kind=getattr(self, "narrator_kind", ""),
            )
            return
        if time.monotonic() >= self.note_until:
            self.countdown.configure(text=self._next_text(remaining, now))

    def _heads_up_on(self) -> bool:
        """The heads-up is sent for this wait: it is switched on and the wait is long enough to fit it."""
        if not self.heads_up.get():
            return False
        return getattr(self, "start_heads_up", False) or getattr(self, "wait_total", 0) >= 2 * update_scene.HEADS_UP_SECONDS

    def _maybe_heads_up(self, remaining: int) -> None:
        """One minute before a change, tell her it is coming and how to stay or pick something else."""
        if getattr(self, "warned", True) or not self._heads_up_on():
            return
        if remaining > update_scene.HEADS_UP_SECONDS or remaining <= 15:
            return
        self.warned = True
        self.heads_up_busy = True
        message = update_scene.heads_up_message(update_scene.duration_text(remaining))
        threading.Thread(target=self._heads_up_work, args=(message,), daemon=True).start()

    def _heads_up_work(self, message: str) -> None:
        try:
            reply, problem = update_scene.narrator_says(message)
            error = ""
        except Exception as caught:
            reply, problem, error = "", "", str(caught)
        try:
            self.after(0, lambda: self._heads_up_done(message, reply, problem, error))
        except tk.TclError:
            pass

    def _heads_up_done(self, message: str, reply: str, problem: str, error: str) -> None:
        self.heads_up_busy = False
        if error:
            self.log(f"Could not give the heads-up. {error}")
            return
        self.log(f"Narrator gave the heads-up: {message.splitlines()[0]}")
        if reply:
            self.log(f"Lora: {reply}")
            # Her answer comes straight back, so a command in it counts without reading the chat.
            answer = {"sender": "ai", "display_name": "", "message": reply, "timestamp": int(time.time() * 1000)}
            self._take_command(update_scene.narrator_command_from([answer]))
        if problem:
            self.log(problem)
        self.note("Narrator told her a change is coming.")

    def _save_heads_up(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["heads_up"] = "1" if self.heads_up.get() else "0"
        update_scene.save_prefs(prefs)

    def _maybe_mid_check(self, remaining: int) -> None:
        """Every 10 minutes of a long wait, read the chat. A go, weather, or change command happens right away."""
        if self.narrator_peeking or getattr(self, "heads_up_busy", False):
            return
        if remaining <= 3 * 60 or time.monotonic() < getattr(self, "next_mid_check", float("inf")):
            return
        self.next_mid_check = time.monotonic() + MID_CHECK_SECONDS
        self.narrator_peeking = True
        threading.Thread(target=self._peek_narrator, args=(True,), daemon=True).start()

    def _peek_narrator(self, mid: bool = False) -> None:
        try:
            messages = update_scene.recent_messages(self.narrator_after, pages=8)
            command = update_scene.narrator_command_from(messages)
            error = ""
        except Exception as caught:
            command = ("", "", "", 0)
            error = str(caught)
        self.after(0, lambda: self._peek_done(command, error, mid))

    def _peek_done(self, command: tuple[str, str, str, int], error: str, mid: bool = False) -> None:
        self.narrator_peeking = False
        if error:
            self.log(f"Could not read the chat. {error}")
            return
        taken = self._take_command(command)
        if mid and taken and self.phase == "waiting" and self.narrator_kind in ("go", "weather", "change"):
            options = getattr(self, "pending", None)
            if options:
                self.shift_setting(
                    options,
                    request=self.narrator_request,
                    requested_by=self.narrator_speaker,
                    checked=True,
                    kind=self.narrator_kind,
                )

    def _take_command(self, command: tuple[str, str, str, int]) -> bool:
        """Keep the newest command. One that is older, or an extend that was already used, is ignored."""
        kind, speaker, request, stamp = command
        if not kind or stamp <= getattr(self, "command_stamp", 0):
            return False
        if kind == "extend" and stamp <= simulation.last_extend_ms():
            return False
        self.command_stamp = stamp
        self.narrator_kind = kind
        self.narrator_request = request if kind in ("go", "weather", "change") else ""
        self.narrator_speaker = speaker
        self.narrator_extend_at = stamp if kind == "extend" else 0
        if self.phase != "waiting":
            return True
        who = speaker or "Lora"
        if kind == "go":
            self.log(f"Narrator has a request from {speaker or 'the chat'}: {request}")
            self.note("Narrator has a request.")
        elif kind == "weather":
            self.log(f"{who} asked Narrator to change the weather to {request}.")
            self.note(f"Narrator will change the weather to {request}.")
        elif kind == "change":
            self.log(f"{who} asked Narrator to change something: {request}")
            self.note("Narrator will change something around her.")
        else:
            self.log(f"{who} asked Narrator to extend her time.")
            self.note("Narrator will extend her time.")
        return True

    def shift_setting(
        self,
        options: dict,
        morning: bool = False,
        request: str = "",
        requested_by: str = "",
        checked: bool = False,
        extend_at: int = 0,
        kind: str = "",
    ) -> None:
        self._show_compact(False)
        if self.phase == "busy":
            return
        self.phase = "busy"
        self.ask_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.countdown.configure(text="Changing what she is doing.")
        prior = self.prior.get("1.0", "end").strip()
        environment = self.environment.get("1.0", "end").strip()
        backstory = self.backstory.get("1.0", "end").strip()
        self.log("Simulation step started.")
        threading.Thread(
            target=self._shift_work,
            args=(prior, options, environment, backstory, morning, request, requested_by, checked, extend_at, kind),
            daemon=True,
        ).start()

    def _shift_work(
        self,
        prior: str,
        options: dict,
        environment: str,
        backstory: str,
        morning: bool,
        request: str,
        requested_by: str = "",
        checked: bool = False,
        extend_at: int = 0,
        kind: str = "",
    ) -> None:
        schedule = {"every": options["minutes"], "times": options.get("times") or [], "ai": bool(options.get("ai_minutes"))}
        try:
            result = simulation.advance_simulation(
                prior,
                environment,
                backstory,
                morning,
                request,
                requested_by,
                check_chat=not checked,
                schedule=schedule,
                extend_at=extend_at,
                stay_here=kind == "change",
                weather=request if kind == "weather" else "",
            )
            error = ""
        except Exception as caught:
            result = None
            error = str(caught)
        self.after(0, lambda: self._shift_done(result, error, options))

    def _shift_done(self, result: dict | None, error: str, options: dict) -> None:
        if result and result.get("setting"):
            self.set_setting(result["setting"])
        if error or not result:
            self.log(f"Could not change what she is doing. {error}".strip())
        elif result.get("pending"):
            self.log(f"Saved here, Kindroid update pending: {result.get('error', '')}")
            self.note("Saved here. Kindroid update pending.")
        elif result.get("extended"):
            if result.get("error"):
                self.log(result["error"])
            self.log(f"She stays a little longer: {result['setting']}")
        else:
            if result.get("error"):
                self.log(result["error"])
            self.log(f"Changed what she is doing: {result['setting']}")
        if self.stop_requested:
            self.become_idle("Stopped.")
            return
        self._arm_after_turn(options, (result or {}).get("minutes"), (result or {}).get("delay"))

    def arm_wand(self, announce: bool = True) -> None:
        """Tap the wand on its own timer while the simulation is running."""
        self.cancel_wand()
        if self.phase == "idle" or not self.wand_on.get():
            return
        try:
            minutes = wand.talk_minutes(self.wand_minutes.get())
        except RuntimeError as error:
            self._say(str(error))
            return
        self._save_wand_prefs()
        if announce:
            self._say(f"Mochi will tap the wand every {minutes} minutes.")
        self.wand_id = self.after(int(minutes * 60 * 1000), self._wand_tick)

    def cancel_wand(self) -> None:
        if self.wand_id is not None:
            try:
                self.after_cancel(self.wand_id)
            except tk.TclError:
                pass
            self.wand_id = None

    def _toggle_wand(self) -> None:
        self._save_wand_prefs()
        if self.phase == "idle":
            return
        if self.wand_on.get():
            self.arm_wand()
        else:
            self.cancel_wand()
            self.log("Mochi will stop tapping the wand.")

    def _save_wand_prefs(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["wand"] = "1" if self.wand_on.get() else "0"
        prefs["wand_minutes"] = self.wand_minutes.get().strip()
        update_scene.save_prefs(prefs)

    def _wand_tick(self) -> None:
        self.wand_id = None
        if self.phase == "idle" or not self.wand_on.get():
            return
        threading.Thread(target=self._wand_work, daemon=True).start()
        self.arm_wand(announce=False)

    def _wand_work(self) -> None:
        if not self.wand_gate.acquire(blocking=False):
            return
        try:
            text = wand.tap()
            error = ""
        except Exception as caught:
            text = ""
            error = str(caught)
        finally:
            self.wand_gate.release()
        try:
            self.after(0, lambda text=text, error=error: self._wand_done(text, error))
        except tk.TclError:
            pass

    def _wand_done(self, text: str, error: str) -> None:
        if not self.winfo_exists():
            return
        if error:
            self.log(f"Could not tap the wand. {error}")
            return
        self.note("Mochi sent a message with the wand.")
        self.log(f"Mochi sent a wand message: {text}")

    def stop(self) -> None:
        self.stop_requested = True
        self.cancel_wand()
        self.queued_call = None
        self.stopped_on = update_scene.la_now().date().isoformat()
        simulation.set_schedule(False)
        if self.phase == "waiting":
            self.become_idle("Stopped.")
            return
        self.countdown.configure(text="Stopping after this change.")

    def become_idle(self, status: str, restore_master: bool = False) -> None:
        self.cancel_wand()
        self.phase = "idle"
        self.pending = None
        self.countdown.configure(text=status)
        self.ask_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._show_compact(True)
        if restore_master:
            threading.Thread(target=self._restore_master, daemon=True).start()

    def _restore_master(self) -> None:
        try:
            name = update_scene.activate_named_profile("master_profile")
            error = ""
        except Exception as caught:
            name = ""
            error = str(caught)
        self.after(0, lambda: self._restore_master_done(name, error))

    def _restore_master_done(self, name: str, error: str) -> None:
        if error:
            self.log(error)
            return
        self.log(f"Chatting as {name}.")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
