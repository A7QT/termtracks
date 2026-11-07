"""Track records and tag reading for the app."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".opus", ".m4a", ".ogg", ".oga", ".wav", ".aac", ".wma"}
)


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    path: Path
    album: str = ""
    duration: float | None = None
    source: str = ""
    source_url: str = ""

    @property
    def duration_str(self) -> str:
        return format_duration(self.duration)

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}"


def format_duration(seconds: float | None) -> str:
    """Render seconds as m:ss (or h:mm:ss past an hour)."""
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def read_tags(path: Path) -> dict[str, object]:
    """Pull tags and duration out of an audio file via mutagen."""
    try:
        from mutagen import File as MutagenFile
    except ImportError:  # pragma: no cover - mutagen is a hard dependency
        return {}

    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return {}
    if audio is None:
        return {}

    def first(key: str) -> str:
        value = audio.get(key) if hasattr(audio, "get") else None
        if not value:
            return ""
        if isinstance(value, list):
            return str(value[0]) if value else ""
        return str(value)

    duration = None
    info = getattr(audio, "info", None)
    if info is not None and getattr(info, "length", None):
        duration = float(info.length)

    return {
        "title": first("title"),
        "artist": first("artist"),
        "album": first("album"),
        "duration": duration,
    }
