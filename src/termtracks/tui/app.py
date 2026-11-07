"""The termtracks TUI: search it, grab it, file it into ~/Music."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from termtracks import __version__, download, preview, search
from termtracks.clipboard import read_clipboard, read_primary
from termtracks.config import (
    AUDIO_FORMATS,
    Config,
    LibraryConfig,
    load_config,
    save_config,
)
from termtracks.download import DownloadProgress, harvest_result
from termtracks.search import SearchResult

SOURCE_COLORS = {
    "youtube": "red",
    "youtube-web": "#ff4d4d",
    "soundcloud": "#ff8800",
}
MUTED = "grey58"
SUCCESS = "green"
ERROR = "red"
WARNING = "gold"

ACCENT = "cyan"


def progress_bar(fraction: float, width: int) -> str:
    fraction = min(1.0, max(0.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "╺" + "─" * max(0, width - filled - 1)


@dataclass
class DownloadJob:
    id: int
    label: str
    url: str = ""
    status: str = "queued"
    percent: float = 0.0
    detail: str = ""
    last_update: float = field(default=0.0, repr=False)


def _fit(text: Text, width: int) -> Text:
    """Clip a cell so auto-sized columns cannot overflow the viewport."""
    if len(text) > width:
        text.truncate(width, overflow="ellipsis")
    return text


class PasteableInput(Input):
    """Input that pastes from the OS clipboard / primary selection.

    Textual captures the mouse, so the terminal's native middle-click paste
    never fires. Middle-click reads the primary selection; Ctrl+V reads the
    system clipboard (falling back to Textual's in-app clipboard).
    """

    def _insert_paste_text(self, text: str) -> None:
        if not text:
            return
        line = text.splitlines()[0].strip()
        if not line:
            return
        start, end = self.selection
        self.replace(line, start, end)

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 2:  # middle click → primary selection
            event.stop()
            self.focus()
            offset_x, _ = event.get_content_offset_capture(self)
            index = self._cell_offset_to_index(offset_x)
            self.selection = type(self.selection)(index, index)
            self._insert_paste_text(read_primary())
            return
        await super()._on_mouse_down(event)

    def action_paste(self) -> None:
        text = read_clipboard() or self.app.clipboard
        self._insert_paste_text(text)


class ResultTable(DataTable[Text]):
    """Search results from YouTube / SoundCloud."""

    DEFAULT_CSS = """
    ResultTable {
        height: 1fr;
        scrollbar-size-vertical: 1;
    }
    """

    # index + source + time + owned marker
    FIXED_WIDTH = 4 + 6 + 6 + 3

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.results: list[SearchResult] = []
        self._owned: set[str] = set()
        self._budgets = (40, 24)
        # Set on every left click so RowSelected messages fired by a click
        # (instead of the enter key) can be told apart and ignored - the
        # double-click handler owns mouse activation.
        self._last_click = 0.0

    def on_mount(self) -> None:
        self.add_column("#", width=4, key="index")
        self.add_column("Src", width=6, key="source")
        self.add_column("Title", key="title")
        self.add_column("Artist", key="artist")
        self.add_column("Time", width=6, key="time")
        self.add_column("", width=3, key="owned")

    def _measure(self) -> tuple[int, int]:
        flex = max(30, (self.size.width or 80) - 2 - self.FIXED_WIDTH - 12)
        artist = min(28, max(12, int(flex * 0.35)))
        return max(12, flex - artist), artist

    def on_resize(self) -> None:
        budgets = self._measure()
        if budgets != self._budgets:
            self._budgets = budgets
            if self.results:
                self.set_results(self.results, self._owned)

    def set_results(self, results: list[SearchResult], owned: set[str]) -> None:
        title_width, artist_width = self._measure()
        self._budgets = (title_width, artist_width)
        self.results = list(results)
        self._owned = owned
        self.clear()
        for position, result in enumerate(self.results, start=1):
            color = SOURCE_COLORS.get(result.source, MUTED)
            already_have = (result.url or "") in owned
            row_style = SUCCESS if already_have else color
            cell_muted = SUCCESS if already_have else MUTED
            self.add_row(
                Text(str(position), style=cell_muted),
                Text(result.label, style=f"bold {row_style}"),
                _fit(Text(result.title, style=row_style), title_width),
                _fit(Text(result.artist, style=row_style), artist_width),
                Text(result.duration_str, style=cell_muted),
                Text("✓" if already_have else "", style=SUCCESS),
                height=1,
            )
        if self.results:
            self.move_cursor(row=0)

    @property
    def selected_result(self) -> SearchResult | None:
        if not self.results:
            return None
        index = self.cursor_row
        if 0 <= index < len(self.results):
            return self.results[index]
        return None

    def on_click(self, event: events.Click) -> None:
        """Double-click previews (again to save); right-click stops a preview."""
        if event.button == 3:
            event.stop()  # keep the right click from moving the cursor
            app = self.app
            if isinstance(app, DownloaderApp):
                app.stop_preview()
            return
        if event.button != 1:
            return
        self._last_click = time.monotonic()
        if event.chain != 2:
            return
        row = event.style.meta.get("row")
        if not isinstance(row, int) or not 0 <= row < len(self.results):
            return
        app = self.app
        if isinstance(app, DownloaderApp):
            with suppress(Exception):
                self.move_cursor(row=row)
            app.toggle_preview()


class SearchPane(Vertical):
    """Query box on top, results underneath."""

    DEFAULT_CSS = """
    SearchPane {
        height: 1fr;
    }
    SearchPane Input {
        height: 3;
        border: tall $primary-darken-3;
        background: $surface;
        margin-bottom: 1;
    }
    SearchPane Input:focus {
        border: tall $accent;
        background: $surface;
    }
    SearchPane .pane-hint {
        height: 1;
        color: $text-muted;
        text-style: italic;
        opacity: 0.65;
    }
    """

    def compose(self) -> ComposeResult:
        yield PasteableInput(
            placeholder="Search YouTube Music or SoundCloud, or paste a link…",
            id="search-input",
        )
        yield Static("", classes="pane-hint", id="search-status")
        yield ResultTable(id="search-results")

    @property
    def table(self) -> ResultTable:
        return self.query_one("#search-results", ResultTable)

    def focus_query(self) -> None:
        self.query_one("#search-input", Input).focus()

    def set_status(self, message: str) -> None:
        self.query_one("#search-status", Static).update(message)


class DownloadsPane(Vertical):
    """Progress for in-flight and finished downloads."""

    DEFAULT_CSS = """
    DownloadsPane {
        height: auto;
        max-height: 8;
        padding: 0 1;
        border-top: solid $primary-darken-3;
        background: $surface;
    }
    DownloadsPane #downloads-title {
        height: 1;
        color: $text-muted;
        text-style: bold;
    }
    DownloadsPane #downloads-log {
        height: auto;
        max-height: 6;
        overflow-y: auto;
    }
    DownloadsPane #downloads-log.empty {
        color: $text-muted;
        text-style: italic;
        opacity: 0.65;
    }
    """

    EMPTY = "Nothing yet."

    def compose(self) -> ComposeResult:
        yield Static("Downloads", id="downloads-title")
        yield Static(self.EMPTY, id="downloads-log", classes="empty")

    def render_jobs(self, jobs: list[DownloadJob]) -> None:
        log = self.query_one("#downloads-log", Static)
        if not jobs:
            log.update(self.EMPTY)
            log.add_class("empty")
            return
        log.remove_class("empty")
        body = Text()
        for job in jobs:
            body.append_text(_render_job(job))
            body.append("\n")
        log.update(body)


def _render_job(job: DownloadJob) -> Text:
    icons = {
        "queued": ("…", MUTED),
        "downloading": ("↓", ACCENT),
        "converting": ("⚙", WARNING),
        "done": ("✓", SUCCESS),
        "error": ("✗", ERROR),
    }
    icon, style = icons.get(job.status, ("·", MUTED))

    line = Text(f"{icon} ", style=style)
    line.append(job.label)
    if job.status in {"downloading", "converting"}:
        line.append(
            f"  {progress_bar(job.percent / 100, 18)} {job.percent:5.1f}%", style=style
        )
    if job.detail:
        line.append(f"  {job.detail}", style="dim")
    return line


class FormatScreen(ModalScreen[None]):
    """Quick settings: pick your preferred file format."""

    DEFAULT_CSS = """
    FormatScreen {
        align: center middle;
        background: #000000 60%;
    }
    FormatScreen #format-dialog {
        width: 52;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $accent;
    }
    FormatScreen .dialog-title {
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }
    FormatScreen .dialog-hint {
        height: 1;
        color: $text-muted;
        text-style: italic;
        opacity: 0.65;
    }
    DirectoryScreen {
        align: center middle;
        background: #000000 60%;
    }
    DirectoryScreen #dir-dialog {
        width: 56;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $accent;
    }
    DirectoryScreen Input {
        border: tall $primary-darken-3;
    }
    DirectoryScreen Input:focus {
        border: tall $accent;
    }
    DirectoryScreen .directory-help {
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }
    DirectoryScreen .dialog-hint {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        text-style: italic;
        opacity: 0.65;
    }
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    DIRECTORY_OPTION = "directory"

    def _options(self) -> list[Option]:
        app = self.app
        assert isinstance(app, DownloaderApp)
        preferred = app.config.download.format
        folder = str(app.config.library_path).replace(str(Path.home()), "~", 1)
        rows = [Option(f"Directory  {folder}", id=self.DIRECTORY_OPTION)]
        rows.extend(
            Option(
                f"{'✓ ' if fmt == preferred else '  '}{fmt}"
                f"{'  (default)' if fmt == 'any' else ''}",
                id=fmt,
            )
            for fmt in AUDIO_FORMATS
        )
        return rows

    def compose(self) -> ComposeResult:
        with Vertical(id="format-dialog"):
            yield Static("Settings", classes="dialog-title")
            yield OptionList(*self._options(), id="format-list")
            yield Static(
                "formats fetched as-is - never re-encoded · enter to edit",
                classes="dialog-hint",
            )

    def _rebuild(self) -> None:
        """Re-render the option rows after a setting changed."""
        option_list = self.query_one("#format-list", OptionList)
        highlighted = option_list.highlighted
        option_list.clear_options()
        option_list.add_options(self._options())
        if highlighted is not None:
            option_list.highlighted = min(highlighted, len(self._options()) - 1)

    def on_click(self, event: object) -> None:
        """Clicking anywhere outside the dialog closes the menu."""
        node = getattr(event, "widget", None)
        while node is not None:
            if getattr(node, "id", None) in ("format-dialog", "dir-dialog"):
                return
            node = node.parent
        self.dismiss()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        app = self.app
        assert isinstance(app, DownloaderApp)
        if event.option_list.id != "format-list" or event.option.id is None:
            return
        option_id = str(event.option.id)
        if option_id == self.DIRECTORY_OPTION:
            app.push_screen(
                DirectoryScreen(app.config.library_path),
                callback=self._apply_directory,
            )
            return
        app.config.download.format = option_id
        save_config(app.config)
        app.notify(f"Preferred format: {option_id}")
        self.dismiss()

    def _apply_directory(self, new_path: str | None) -> None:
        """Save the directory typed into the DirectoryScreen."""
        app = self.app
        assert isinstance(app, DownloaderApp)
        if not new_path:
            return
        folder = Path(new_path)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            app.notify(f"Could not create {folder}: {error}", severity="error")
            return
        app.config.library = LibraryConfig(path=folder)
        save_config(app.config)
        app.notify(f"Saving to {folder}")
        app._refresh_header()
        self._rebuild()


def resolve_directory(text: str) -> tuple[Path | None, str]:
    """Expand and validate a typed download directory.

    Returns (path, ""). On any problem returns (None, reason).

    Discipline rules:
    - "~" is expanded normally.
    - An absolute path whose FIRST segment names an existing subdirectory of
      home (case-insensitive) is re-rooted there: "/Music" → "/home/user/Music".
      Anything else is taken literally - no guessing, no rewriting.
    - Relative paths are rejected outright so the saved path can never depend
      on whatever cwd the app happened to start in.
    - The nearest existing ancestor must be writable by the current user.
    """
    raw = (text or "").strip()
    if not raw:
        return None, "Type a directory first."

    path = Path(raw).expanduser()

    if not path.is_absolute():
        return None, "Use ~/Music or a full path to your folder."

    home = Path.home()
    first = path.parts[1] if len(path.parts) > 1 else ""
    rest = path.parts[2:]
    if first and os.name != "nt":
        try:
            match = next(
                (entry for entry in os.listdir(home) if entry.lower() == first.lower()),
                None,
            )
        except OSError:
            match = None
        if match is not None:
            path = home.joinpath(match, *rest)

    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not os.access(probe, os.R_OK | os.W_OK | os.X_OK):
        return None, f"No write permission: {probe}"

    return path, ""


class DirectoryScreen(ModalScreen[str]):
    """Type a new download directory."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, current: Path) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dir-dialog"):
            yield Static("Download directory", classes="dialog-title")
            yield Static(
                "Choose where future downloads are saved.\n"
                "Type ~/Music or ~/Music/TermTracks (~ means your home folder), "
                "or a full path (C:\\Users\\YourName\\Music on Windows).\n"
                "On Linux/macOS, /Music uses ~/Music if Music already exists "
                "in your home folder.\n"
                "Missing folders are created when you save. "
                "Existing songs stay where they are.",
                classes="directory-help",
                id="dir-help",
                markup=False,
            )
            yield Input(value=str(self.current), id="dir-input")
            yield Static(
                "enter saves · esc cancels",
                classes="dialog-hint",
                id="dir-hint",
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "dir-input":
            return
        path, error = resolve_directory(event.value)
        if path is None:
            hint = self.query_one("#dir-hint", Static)
            hint.update(error)
            hint.styles.color = "red"
            event.input.focus()
            return
        self.dismiss(str(path))


class DownloaderApp(App[None]):
    """Search YouTube and SoundCloud, download straight into your music folder."""

    TITLE = "TermTracks"
    SUB_TITLE = f"v{__version__}"

    CSS = """
    #body {
        height: 1fr;
        padding: 1 2;
    }
    Footer {
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("ctrl+o", "format", "Settings"),
        Binding("escape", "leave_input", "Leave field", show=False),
        Binding("d", "download", "Download", show=False),
        Binding("delete", "delete_track", "Delete", show=False),
        Binding("q,ctrl+c", "quit", "Quit"),
    ]

    def action_format(self) -> None:
        self.push_screen(FormatScreen())

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or load_config()
        self.jobs: list[DownloadJob] = []
        self._next_job_id = 1
        self.preview_session: preview.PreviewSession | None = None

    def on_unmount(self) -> None:
        if self.preview_session is not None:
            self.preview_session.stop()

    # -- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="body"):
            yield SearchPane(id="search")
            yield DownloadsPane(id="downloads")
        yield Footer()

    def _refresh_header(self) -> None:
        """Keep the saved count in the header honest."""
        folder = str(self.config.library_path).replace(str(Path.home()), "~", 1)
        saved = len(download.owned_urls())
        self.sub_title = f"{folder} · {saved} saved"

    def on_mount(self) -> None:
        self.config.library_path.mkdir(parents=True, exist_ok=True)
        self._refresh_header()
        self.query_one("#search", SearchPane).focus_query()

    # -- helpers ---------------------------------------------------------

    @property
    def search_pane(self) -> SearchPane:
        return self.query_one("#search", SearchPane)

    @property
    def downloads_pane(self) -> DownloadsPane:
        return self.query_one("#downloads", DownloadsPane)

    @property
    def results_table(self) -> ResultTable:
        return self.query_one("#search-results", ResultTable)

    def owned_urls(self) -> set[str]:
        """URLs previously downloaded whose files still exist."""
        return download.owned_urls()

    def _safe_call(self, callback: Callable[..., object], *args: object) -> None:
        """Hop from a worker thread back onto the UI thread."""
        with suppress(RuntimeError):
            self.call_from_thread(callback, *args)

    # -- search ----------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            query = event.value.strip()
            if query:
                self.search_pane.set_status(f"Searching for “{query}”…")
                self._search_worker(query)

    @work(thread=True, exclusive=True, group="search")
    def _search_worker(self, query: str) -> None:
        try:
            results, from_cache = search.cached_search(query, config=self.config)
            self._safe_call(self._on_search_done, query, results, "", from_cache)
        except Exception as error:
            self._safe_call(self._on_search_done, query, [], str(error), False)

    def _on_search_done(
        self,
        query: str,
        results: list[SearchResult],
        error: str,
        from_cache: bool = False,
    ) -> None:
        if error:
            self.search_pane.set_status(f"Search failed: {error}")
            self.notify(f"Search failed: {error}", severity="error")
            return
        self.results_table.set_results(results, self.owned_urls())
        if not results:
            self.search_pane.set_status("Nothing found.")
            return
        suffix = " - cached" if from_cache else ""
        self.search_pane.set_status(
            f"{len(results)} results{suffix} - enter to preview, again to save."
        )
        self.results_table.focus()

    # -- downloads -------------------------------------------------------

    def action_download(self) -> None:
        focused = self.screen.focused
        if focused is not None and not isinstance(focused, (ResultTable, Input)):
            return
        result = self.results_table.selected_result
        if result is None:
            return
        if result.is_local or not result.url:
            return
        self.stop_preview()
        job = DownloadJob(
            id=self._next_job_id,
            label=f"{result.artist} - {result.title}",
            url=result.url or "",
        )
        self._next_job_id += 1
        self.jobs.insert(0, job)
        self._refresh_downloads()
        self.notify(f"Downloading {job.label}")
        self._download_worker(job, result)

    @work(thread=True, group="download")
    def _download_worker(self, job: DownloadJob, result: SearchResult) -> None:
        def progress(update: DownloadProgress) -> None:
            now = time.monotonic()
            if update.status == "downloading" and now - job.last_update < 0.2:
                return
            job.last_update = now
            job.status = "downloading" if update.status == "finished" else update.status
            job.percent = update.percent
            job.detail = update.speed_str or update.message
            self._safe_call(self._refresh_downloads)

        try:
            track = harvest_result(result, config=self.config, progress=progress)
        except Exception as error:
            job.status = "error"
            job.detail = str(error).splitlines()[0][:120]
            self._safe_call(self._on_download_failed, job)
            return

        job.status = "done"
        job.percent = 100.0
        job.detail = str(track.path.relative_to(self.config.library_path))
        self._safe_call(self._on_download_done, job)

    def _on_download_done(self, job: DownloadJob) -> None:
        self._refresh_downloads()
        self._refresh_header()
        # Re-check the registry so finished downloads show the checkmark.
        self.results_table.set_results(self.results_table.results, self.owned_urls())
        self.search_pane.set_status(f"Saved {job.label}.")
        self.notify(f"Saved {job.label}", severity="information")

    def _on_download_failed(self, job: DownloadJob) -> None:
        self._refresh_downloads()
        self.search_pane.set_status(f"Download failed: {job.detail}")
        self.notify(f"Download failed: {job.detail}", severity="error")

    def _refresh_downloads(self) -> None:
        with suppress(Exception):
            self.downloads_pane.render_jobs(self.jobs)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not isinstance(event.data_table, ResultTable):
            return
        # Clicks also post RowSelected; the double-click handler already
        # acted, so a second toggle here would stop the preview and download.
        if time.monotonic() - event.data_table._last_click < 0.5:
            return
        self.toggle_preview()

    # -- preview ---------------------------------------------------------

    def toggle_preview(self) -> None:
        """Enter / double-click: preview, or save if that row is playing."""
        result = self.results_table.selected_result
        if result is None or result.is_local or not result.url:
            return
        active = self.preview_session
        if active is not None and active.result == result:
            self.stop_preview()
            self.action_download()
            return
        self.start_preview(result)

    def start_preview(self, result: SearchResult) -> None:
        if not preview.player_available():
            self.notify(
                "Preview needs ffplay and ffmpeg on your PATH", severity="error"
            )
            return
        self.stop_preview()
        session = preview.PreviewSession(
            result,
            config=self.config,
            local_path=download.get_registered(result.url or ""),
            on_status=lambda message: self._safe_call(
                self.search_pane.set_status, message
            ),
        )
        self.preview_session = session
        session.start()

    def stop_preview(self) -> None:
        if self.preview_session is None:
            return
        self.preview_session.stop()
        self.preview_session = None
        with suppress(Exception):
            self.search_pane.set_status("Preview stopped.")

    # -- delete ----------------------------------------------------------

    def action_delete_track(self) -> None:
        """Delete the highlighted track's saved file (if it has one)."""
        focused = self.screen.focused
        if focused is not None and not isinstance(focused, (ResultTable, Input)):
            return
        result = self.results_table.selected_result
        if result is None or result.is_local or not result.url:
            return
        removed = download.remove_download(result.url)
        if removed is None:
            self.notify(f"Not saved yet: {result.title}")
            return
        self.stop_preview()
        self._refresh_header()
        self.results_table.set_results(self.results_table.results, self.owned_urls())
        self.search_pane.set_status(f"Deleted {removed.name}.")
        self.notify(f"Deleted {removed.name}", severity="warning")

    # -- misc ------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.search_pane.focus_query()

    def action_leave_input(self) -> None:
        """Escape leaves the input, or stops a running preview."""
        if isinstance(self.screen.focused, Input):
            self.results_table.focus()
            return
        self.stop_preview()


def run(config: Config | None = None) -> None:
    DownloaderApp(config).run()
