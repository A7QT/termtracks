"""Chunked previews: download a track 15 seconds at a time and play it with ffplay.

Only the parts you actually listen to are fetched. Hitting enter (or
double-clicking again) stops the preview and saves the full track.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from termtracks.config import Config, load_config
from termtracks.library import format_duration
from termtracks.search import SearchResult

CHUNK_SECONDS = 15.0
# Hard cap for unknown durations so the producer thread cannot run forever.
MAX_CHUNKS = 960

StatusCallback = Callable[[str], None]

PLAYER = "ffplay"


class PreviewError(Exception):
    """Raised when a preview cannot be started or a chunk fails."""


def player_available() -> bool:
    return shutil.which(PLAYER) is not None and shutil.which("ffmpeg") is not None


def _download_chunk(
    url: str, start: float, end: float, temp_dir: Path, index: int
) -> Path:
    """Fetch one [start, end) section of the track's best audio stream."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func

    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "download_ranges": download_range_func(None, [(start, end)]),
        "outtmpl": str(temp_dir / f"chunk-{index}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
        "socket_timeout": 30,
    }
    with YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)

    candidates = [path for path in temp_dir.glob(f"chunk-{index}.*") if path.is_file()]
    if not candidates:
        raise PreviewError("no data left to preview")
    newest = max(candidates, key=lambda path: path.stat().st_mtime)
    if newest.stat().st_size < 1024:
        raise PreviewError("no data left to preview")
    return newest


class PreviewSession:
    """Plays a search result in 15-second chunks.

    A producer thread downloads chunk N+1 while chunk N is playing, so
    playback keeps rolling as long as you keep listening.
    """

    def __init__(
        self,
        result: SearchResult,
        *,
        config: Config | None = None,
        local_path: Path | None = None,
        on_status: StatusCallback | None = None,
    ) -> None:
        self.result = result
        self.config = config or load_config()
        self.local_path = local_path
        self._on_status = on_status
        self._stop = threading.Event()
        self._chunks: queue.Queue[Path | None] = queue.Queue()
        self._player: subprocess.Popen[bytes] | None = None
        self._player_lock = threading.Lock()
        self._position_lock = threading.Lock()
        self._chunk_start = 0.0
        self._play_started: float | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def label(self) -> str:
        return f"{self.result.artist} - {self.result.title}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stop playback and wind the session down."""
        self._stop.set()
        self._kill_player()

    # -- internals -------------------------------------------------------

    def _report(self, message: str) -> None:
        if self._on_status is not None and not self._stop.is_set():
            self._on_status(message)

    def _kill_player(self) -> None:
        with self._player_lock:
            process = self._player
            self._player = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def _run(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="termtracks-preview-"))
        producer = threading.Thread(
            target=self._produce, args=(temp_dir,), daemon=True
        )
        producer.start()
        ticker = threading.Thread(target=self._tick, daemon=True)
        ticker.start()
        played = 0
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._chunks.get(timeout=0.2)
                except queue.Empty:
                    continue
                if chunk is None or self._stop.is_set():
                    break
                self._play(chunk, played * CHUNK_SECONDS)
                played += 1
        finally:
            self._stop.set()
            self._kill_player()
            producer.join(timeout=1)
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _produce(self, temp_dir: Path) -> None:
        if self.local_path is not None:
            self._chunks.put(self.local_path)
            self._chunks.put(None)
            return

        url = self.result.url or ""
        total = self.result.duration
        index = 0
        while not self._stop.is_set() and index < MAX_CHUNKS:
            start = index * CHUNK_SECONDS
            if total is not None and start >= max(total - 0.5, 0.0):
                break
            self._report(f"♪ loading {format_duration(start)} of {self.label}…")
            try:
                chunk = _download_chunk(
                    url, start, start + CHUNK_SECONDS, temp_dir, index
                )
            except Exception as error:
                if index == 0:
                    self._report(f"Preview failed: {str(error).splitlines()[0][:120]}")
                break
            if self._stop.is_set():
                break
            self._chunks.put(chunk)
            index += 1
        self._chunks.put(None)

    def _play(self, chunk: Path, start: float) -> None:
        """Play one chunk, watching for a stop request while it runs."""
        try:
            process = subprocess.Popen(
                [PLAYER, "-autoexit", "-nodisp", "-loglevel", "quiet", str(chunk)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            self._report(f"Preview failed: {error}")
            self._stop.set()
            return

        with self._player_lock:
            if self._stop.is_set():
                process.terminate()
                self._player = None
            else:
                self._player = process
        with self._position_lock:
            self._chunk_start = start
            self._play_started = time.monotonic()
        self._report(f"♪ {format_duration(start)} · {self.label} - enter to save")

        while process.poll() is None and not self._stop.is_set():
            self._stop.wait(timeout=0.2)
        if self._stop.is_set():
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        with self._player_lock:
            if self._player is process:
                self._player = None
        with self._position_lock:
            self._play_started = None

    def _tick(self) -> None:
        """Keep the status line's playback clock honest, once a second."""
        while not self._stop.is_set() and self._stop.wait(timeout=1.0) is not True:
            with self._position_lock:
                started = self._play_started
                chunk_start = self._chunk_start
            if started is None:
                continue
            position = chunk_start + (time.monotonic() - started)
            self._report(f"♪ {format_duration(position)} · {self.label} - enter to save")
