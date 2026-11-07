"""Download engine: fetch audio with yt-dlp, convert with ffmpeg, file it away."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from termtracks import library
from termtracks.config import Config, load_config
from termtracks.search import (
    UNKNOWN_ARTIST,
    SearchResult,
    clean_title,
    split_artist_title,
)

ProgressCallback = Callable[["DownloadProgress"], None]

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
NO_ALBUM = "Singles"

# YouTube's default player client intermittently 403s on the media URLs it
# hands out. These clients serve the same audio when that happens.
_FALLBACK_CLIENTS = ("android_vr", "android")
_RETRYABLE = (
    "403",
    "forbidden",
    "unable to download video data",
    "requested format is not available",
    "fragment",
    "timed out",
)


class DownloadError(Exception):
    """Raised when a harvest fails."""


@dataclass(frozen=True)
class DownloadProgress:
    status: str
    percent: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    message: str = ""

    @property
    def speed_str(self) -> str:
        if not self.speed:
            return ""
        mb = self.speed / 1_000_000
        if mb >= 1:
            return f"{mb:.1f} MB/s"
        return f"{self.speed / 1000:.0f} KB/s"


def sanitize(name: str, *, fallback: str = "Unknown", limit: int = 120) -> str:
    """Make a string safe to use as a single path component."""
    cleaned = _ILLEGAL.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .")
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip(" .")
    return cleaned or fallback


def _unique_path(path: Path) -> Path:
    """Append a counter until the path is free."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for counter in range(2, 1000):
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
    raise DownloadError(f"Could not find a free filename near {path}")


def destination_for(
    artist: str, album: str, title: str, extension: str, config: Config
) -> Path:
    # Flat: every song lands directly in the library folder.
    del artist, album
    return config.library_path / f"{sanitize(title, fallback='Untitled')}{extension}"


def _postprocessors(config: Config) -> list[dict[str, Any]]:
    """Strip the audio out of its download container.

    With "best" (native), yt-dlp copies the existing stream into a matching
    container (webm/opus becomes .opus) instead of re-encoding it.
    """
    return [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "best",
            "preferredquality": config.download.bitrate,
        },
        {"key": "FFmpegMetadata", "add_metadata": True},
    ]


def _format_selector(config: Config) -> str:
    """Prefer a stream already in the configured format, else the best one.

    Falling through on `ext` never converts anything: if the source has no
    stream with the preferred extension, the plain best stream is taken as-is.
    """
    preferred = config.download.format
    if preferred != "any":
        return f"bestaudio[ext={preferred}]/bestaudio/best"
    return "bestaudio/best"


def _hook_to_progress(payload: dict[str, Any]) -> DownloadProgress:
    status = payload.get("status", "downloading")
    downloaded = int(payload.get("downloaded_bytes") or 0)
    total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
    total = int(total) if total else None
    percent = (downloaded / total * 100) if total else 0.0
    if status == "finished":
        percent = 100.0
    return DownloadProgress(
        status=status,
        percent=percent,
        downloaded_bytes=downloaded,
        total_bytes=total,
        speed=payload.get("speed"),
        eta=payload.get("eta"),
    )


def _write_tags(path: Path, *, title: str, artist: str, album: str) -> None:
    """Normalize tags after ffmpeg, so the files are labeled cleanly."""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path, easy=True)
        if audio is None:
            return
        if audio.tags is None:
            audio.add_tags()
        audio["title"] = title
        audio["artist"] = artist
        if album:
            audio["album"] = album
        audio.save()
    except Exception:
        return


def _extract_metadata(
    info: dict[str, Any], hint: SearchResult | None
) -> tuple[str, str, str]:
    """Settle on (artist, title, album) from yt-dlp info plus any search hint."""
    artist = (info.get("artist") or info.get("creator") or "").strip()
    title = (info.get("track") or "").strip()
    album = (info.get("album") or "").strip()

    raw_title = (info.get("title") or "").strip()
    uploader = (info.get("uploader") or info.get("channel") or "").strip()
    if not (artist and title) and (raw_title or uploader):
        guessed_artist, guessed_title = split_artist_title(raw_title, uploader)
        artist = artist or guessed_artist
        title = title or guessed_title

    if hint is not None:
        # The placeholder is worse than anything the search result knows.
        if not artist or artist == UNKNOWN_ARTIST:
            artist = hint.artist or artist
        title = title or hint.title
        album = album or hint.album

    return (
        artist.strip() or UNKNOWN_ARTIST,
        clean_title(title) or "Untitled",
        album.strip(),
    )


def _is_retryable(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _RETRYABLE)


def _fetch(
    url: str,
    options: dict[str, Any],
    temp_dir: Path,
    progress: ProgressCallback | None,
) -> dict[str, Any] | None:
    """Download `url`, falling back to other YouTube clients on a 403.

    The extractor argument is ignored by every non-YouTube extractor, so other
    sources simply get a plain retry.
    """
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError as YtDlpError

    last_error: Exception | None = None

    for attempt, client in enumerate((None, *_FALLBACK_CLIENTS)):
        if attempt:
            # Clear partial output so _resolve_output cannot pick up a stray file.
            for leftover in temp_dir.iterdir():
                leftover.unlink(missing_ok=True)
            if progress is not None:
                progress(
                    DownloadProgress(status="downloading", message=f"Retrying ({client})")
                )

        current = dict(options)
        if client is not None:
            current["extractor_args"] = {"youtube": {"player_client": [client]}}

        try:
            with YoutubeDL(current) as ydl:
                return ydl.extract_info(url, download=True)
        except YtDlpError as error:
            last_error = error
            if not _is_retryable(error):
                break

    raise DownloadError(str(last_error))


def _resolve_output(temp_dir: Path) -> Path:
    candidates = [path for path in temp_dir.iterdir() if path.is_file()]
    if not candidates:
        raise DownloadError("yt-dlp produced no output file")
    # Conversion can leave the original alongside the result; take the newest.
    return max(candidates, key=lambda path: path.stat().st_mtime)


def download_url(
    url: str,
    *,
    config: Config | None = None,
    hint: SearchResult | None = None,
    progress: ProgressCallback | None = None,
    source: str = "",
) -> library.Track:
    """Download a single track URL into the music folder."""
    config = config or load_config()

    def report(payload: dict[str, Any]) -> None:
        if progress is not None:
            progress(_hook_to_progress(payload))

    def report_pp(payload: dict[str, Any]) -> None:
        if progress is not None and payload.get("status") == "started":
            progress(
                DownloadProgress(status="converting", percent=100.0, message="Converting")
            )

    with tempfile.TemporaryDirectory(prefix="termtracks-") as temp_name:
        temp_dir = Path(temp_name)
        options: dict[str, Any] = {
            "format": _format_selector(config),
            "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
            "postprocessors": _postprocessors(config),
            "progress_hooks": [report],
            "postprocessor_hooks": [report_pp],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "retries": 3,
            "socket_timeout": 30,
        }

        info = _fetch(url, options, temp_dir, progress)

        if not info:
            raise DownloadError(f"Nothing downloaded from {url}")
        if info.get("entries"):
            info = info["entries"][0]

        artist, title, album = _extract_metadata(info, hint)
        temp_file = _resolve_output(temp_dir)
        # Whatever ffmpeg produced is authoritative: with "native" the codec
        # (and therefore the extension) depends on what the source served.
        extension = temp_file.suffix or ".m4a"

        destination = destination_for(artist, album, title, extension, config)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = _unique_path(destination)
        shutil.move(str(temp_file), destination)

    _write_tags(destination, title=title, artist=artist, album=album or "")

    duration = info.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is None:
        duration = library.read_tags(destination).get("duration")  # type: ignore[assignment]

    track = library.Track(
        title=title,
        artist=artist,
        album=album or NO_ALBUM,
        path=destination,
        duration=duration,
        source=source or (hint.source if hint else "") or _source_from_info(info),
        source_url=info.get("webpage_url") or url,
    )
    record_download(track.source_url, track.path)

    if progress is not None:
        progress(DownloadProgress(status="done", percent=100.0, message=str(track)))
    return track


def _source_from_info(info: dict[str, Any]) -> str:
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    if "soundcloud" in extractor:
        return "soundcloud"
    if "youtube" in extractor:
        return "youtube"
    return extractor


def harvest_result(
    result: SearchResult,
    *,
    config: Config | None = None,
    progress: ProgressCallback | None = None,
) -> library.Track:
    """Download a search result into the music folder."""
    if result.is_local:
        raise DownloadError("Nothing to download for a local result")
    if not result.url:
        raise DownloadError(f"No source URL for {result}")

    registered = get_registered(result.url)
    if registered is not None:
        # Already saved: report the existing file instead of re-fetching.
        tags = library.read_tags(registered)
        track = library.Track(
            title=str(tags.get("title") or registered.stem),
            artist=str(tags.get("artist") or result.artist),
            album=str(tags.get("album") or result.album),
            path=registered,
            duration=tags.get("duration"),
            source=result.source,
            source_url=result.url,
        )
        if progress is not None:
            progress(
                DownloadProgress(
                    status="done", percent=100.0, message="Already downloaded"
                )
            )
        return track

    return download_url(
        result.url,
        config=config,
        hint=result,
        progress=progress,
        source=result.source,
    )


# -- "already downloaded" registry -------------------------------------------

_REGISTRY_NAME = "downloaded.json"


def _registry_path() -> Path:
    from termtracks import config as config_module

    return config_module.CONFIG_DIR / _REGISTRY_NAME


def load_registry() -> dict[str, str]:
    """Map of source URL -> saved file path."""
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_registry(registry: dict[str, str]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def record_download(url: str, path: Path) -> None:
    """Remember a finished download so future searches can mark it."""
    if not url:
        return
    registry = load_registry()
    registry[url] = str(path)
    save_registry(registry)


def owned_urls() -> set[str]:
    """URLs previously downloaded whose files still exist."""
    registry = load_registry()
    return {url for url, text in registry.items() if Path(text).exists()}


def get_registered(url: str) -> Path | None:
    """Path for `url` from the registry, if the file is still there."""
    text = load_registry().get(url)
    if not text:
        return None
    path = Path(text)
    return path if path.exists() else None


def remove_download(url: str) -> Path | None:
    """Delete a saved track's file and forget it in the registry.

    Returns the removed path, or None when nothing was saved for `url`.
    """
    if not url:
        return None
    registry = load_registry()
    text = registry.pop(url, None)
    if text is not None:
        save_registry(registry)
    path = Path(text) if text else None
    if path is None or not path.exists():
        return None
    path.unlink(missing_ok=True)
    return path
