"""Cross-platform music search powered by yt-dlp."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from termtracks.config import CONFIG_DIR, Config
from termtracks.library import format_duration

# `youtube` uses music.youtube.com (catalog songs), `youtube-web` is regular
# YouTube search; soundcloud uses yt-dlp's scsearch.
SOURCE_PREFIXES = {
    "youtube": "https://music.youtube.com/search?q={query}",
    "youtube-web": "ytsearch{n}:{query}",
    "soundcloud": "scsearch{n}:{query}",
}

SOURCE_LABELS = {
    "youtube": "ytm",
    "youtube-web": "yt",
    "soundcloud": "sc",
    "local": "local",
    "url": "url",
}

# Promo noise that shows up in uploaded video titles but not in real metadata.
_NOISE = re.compile(
    r"""\s*[\(\[]\s*(?:
        official(?:\s+(?:music\s+)?(?:video|audio|visualizer|lyric[s]?\s*video))?
        |lyrics?(?:\s+video)?|audio|video|hd|hq|4k|full\s+album|free\s+download
        |visuali[sz]er|m/?v|explicit|clean|remaster(?:ed)?(?:\s*\d{4})?
    )\s*[\)\]]""",
    re.IGNORECASE | re.VERBOSE,
)
_SEPARATORS = (" - ", " – ", " — ", " -- ", " ~ ", " | ")
# Channel-name conventions: "Artist - Topic", "ArtistVEVO", "Artist - Official".
_UPLOADER_SUFFIX = re.compile(
    r"\s*(?:-\s*topic|vevo|-\s*official(?:\s+channel)?)\s*$", re.IGNORECASE
)
UNKNOWN_ARTIST = "Unknown Artist"


@dataclass(frozen=True)
class SearchResult:
    source: str
    title: str
    artist: str
    duration: float | None = None
    url: str | None = None
    album: str = ""
    track_id: int | None = None

    @property
    def duration_str(self) -> str:
        return format_duration(self.duration)

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    @property
    def is_local(self) -> bool:
        return self.source == "local"

    def __str__(self) -> str:
        return f"[{self.label}] {self.artist} - {self.title}"


class SearchError(Exception):
    """Raised when every configured source fails."""


def clean_title(text: str) -> str:
    cleaned = _NOISE.sub("", text or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip(" -–—|")


def clean_uploader(text: str) -> str:
    return _UPLOADER_SUFFIX.sub("", (text or "").strip()).strip()


def split_artist_title(raw_title: str, uploader: str = "") -> tuple[str, str]:
    """Best-effort "Artist - Title" split, falling back to the uploader name."""
    title = clean_title(raw_title)
    for separator in _SEPARATORS:
        if separator in title:
            left, _, right = title.partition(separator)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, clean_title(right)
    return clean_uploader(uploader) or UNKNOWN_ARTIST, title or raw_title.strip()


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text.strip(), re.IGNORECASE))


def _ydl_options(flat: bool) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "ignoreerrors": True,
        "socket_timeout": 20,
    }
    if flat:
        options["extract_flat"] = "in_playlist"
    return options


def _entry_to_result(entry: dict[str, Any], source: str) -> SearchResult | None:
    if not entry:
        return None

    url = entry.get("webpage_url") or entry.get("url") or entry.get("original_url")
    if not url and entry.get("id") and source in ("youtube", "youtube-web"):
        video_id = str(entry["id"])
        # Skip browse/channel/album shelf ids from YTM search pages.
        if not re.fullmatch(r"[\w-]{11}", video_id):
            return None
        host = "music.youtube.com" if source == "youtube" else "www.youtube.com"
        url = f"https://{host}/watch?v={video_id}"
    if not url:
        return None

    # YouTube Music search mixes tracks with artists/albums/playlists.
    if source == "youtube":
        lowered = str(url).lower()
        if "/watch" not in lowered and "youtu.be/" not in lowered:
            return None
        if not (entry.get("title") or entry.get("track")):
            return None

    uploader = entry.get("uploader") or entry.get("channel") or entry.get("creator") or ""
    raw_title = entry.get("title") or "Unknown"

    # YouTube Music and SoundCloud sometimes hand back real tags; trust them.
    artist = (entry.get("artist") or entry.get("creator") or "").strip()
    track = (entry.get("track") or "").strip()
    if artist and track:
        title = track
    else:
        artist, title = split_artist_title(raw_title, uploader)

    duration = entry.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    return SearchResult(
        source=source,
        title=title,
        artist=artist,
        duration=duration,
        url=url,
        album=(entry.get("album") or "").strip(),
    )


def _search_source(query: str, source: str, limit: int) -> list[SearchResult]:
    from yt_dlp import YoutubeDL

    template = SOURCE_PREFIXES.get(source)
    if template is None:
        return []

    options = _ydl_options(flat=True)
    if source == "youtube":
        target = template.format(query=quote_plus(query))
        # Fetch extra shelf items so we still hit `limit` after filtering non-tracks.
        options["playlistend"] = max(limit * 3, limit)
    elif source == "youtube-web":
        # ytsearchN returns N entries - ask for extra in case some lack titles.
        target = template.format(n=limit * 2, query=query)
        options["playlistend"] = limit * 2
    else:
        target = template.format(n=limit, query=query)

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(target, download=False)

    entries = (info or {}).get("entries") or []
    results = []
    for entry in entries:
        result = _entry_to_result(entry, source)
        if result is not None:
            results.append(result)
        if len(results) >= limit:
            break
    return results


def resolve_url(url: str) -> list[SearchResult]:
    """Expand a direct link (single track or playlist) into search results."""
    from yt_dlp import YoutubeDL

    with YoutubeDL(_ydl_options(flat=True)) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []

    extractor = (info.get("extractor_key") or "").lower()
    source = "soundcloud" if "soundcloud" in extractor else "youtube"
    if "youtube" not in extractor and "soundcloud" not in extractor:
        source = "url"

    if info.get("entries"):
        results = []
        for entry in info["entries"]:
            result = _entry_to_result(entry, source)
            if result is not None:
                results.append(result)
        return results

    result = _entry_to_result(info, source)
    return [result] if result else []


def search(
    query: str,
    *,
    sources: tuple[str, ...] | None = None,
    limit: int | None = None,
    config: Config | None = None,
) -> list[SearchResult]:
    """Search the configured remote sources."""
    query = query.strip()
    if not query:
        return []

    from termtracks.config import load_config

    config = config or load_config()
    sources = sources or config.download.sources
    limit = limit or config.download.results_per_source

    results: list[SearchResult] = []

    if is_url(query):
        results.extend(resolve_url(query))
    else:
        errors: list[Exception] = []
        with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
            futures = {
                pool.submit(_search_source, query, source, limit): source
                for source in sources
            }
            per_source: dict[str, list[SearchResult]] = {}
            for future, source in futures.items():
                try:
                    per_source[source] = future.result()
                except Exception as error:
                    errors.append(error)
                    per_source[source] = []

        if errors and not any(per_source.values()):
            raise SearchError(str(errors[0]))

        # Interleave sources so neither platform dominates the top of the list.
        ordered = [per_source.get(source, []) for source in sources]
        for row in range(max((len(items) for items in ordered), default=0)):
            for items in ordered:
                if row < len(items):
                    results.append(items[row])

    return results


# -- search cache ------------------------------------------------------------

_CACHE_NAME = "search-cache.json"
# Cache entries kept on disk; the oldest are evicted once this is exceeded.
CACHE_LIMIT = 200


def _cache_key(query: str, sources: tuple[str, ...], limit: int) -> str:
    """Cache identity: the query plus the settings that shaped the results."""
    normalized = " ".join(query.strip().casefold().split())
    return f"{normalized}|{','.join(sources)}|{limit}"


def _cache_path() -> Path:
    return CONFIG_DIR / _CACHE_NAME


def load_cache() -> dict[str, dict[str, Any]]:
    """Map of cache key -> {"time": float, "results": [dict, ...]}."""
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def read_cache(key: str) -> list[SearchResult] | None:
    """Cached results for `key`, or None when nothing usable is stored."""
    entry = load_cache().get(key)
    if not isinstance(entry, dict):
        return None
    raw = entry.get("results")
    if not isinstance(raw, list):
        return None
    results = []
    for item in raw:
        try:
            results.append(SearchResult(**item))
        except TypeError:
            return None  # an entry from an older format - treat as a miss
    return results


def write_cache(key: str, results: list[SearchResult]) -> None:
    """Store results under `key`, evicting the oldest entries past the cap."""
    cache = load_cache()
    cache.pop(key, None)  # a re-put entry moves to the (newest) end
    cache[key] = {
        "time": time.time(),
        "results": [asdict(result) for result in results],
    }
    if len(cache) > CACHE_LIMIT:
        cache = dict(list(cache.items())[-CACHE_LIMIT:])
    save_cache(cache)


def cached_search(
    query: str,
    *,
    config: Config | None = None,
) -> tuple[list[SearchResult], bool]:
    """search() with a persistent cache.

    Returns (results, from_cache); only successful, non-empty searches are
    cached, so a network hiccup never poisons the cache with an empty page.
    """
    from termtracks.config import load_config

    config = config or load_config()
    key = _cache_key(
        query, config.download.sources, config.download.results_per_source
    )
    cached = read_cache(key)
    if cached is not None:
        return cached, True
    results = search(query, config=config)
    if results:
        write_cache(key, results)
    return results, False
