"""Load and persist termtracks configuration from ~/.config/termtracks/config.toml."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _config_dir() -> Path:
    override = os.environ.get("TERMTRACKS_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "termtracks"


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_LIBRARY_PATH = Path.home() / "Music"

# Preferred source format: when set, the app tries to fetch a stream
# that is ALREADY in this format (no re-encoding ever happens); otherwise it
# takes the best stream the source serves.
AUDIO_FORMATS = ("any", "opus", "mp3", "m4a", "flac", "wav", "vorbis")
FORMAT_ALIASES = {
    "native": "any",
    "best": "any",
    "source": "any",
    "copy": "any",
}
SEARCH_SOURCES = ("youtube", "youtube-web", "soundcloud")


@dataclass
class LibraryConfig:
    path: Path = field(default_factory=lambda: DEFAULT_LIBRARY_PATH)


@dataclass
class DownloadConfig:
    format: str = "any"
    bitrate: str = "192"
    threads: int = 4
    sources: tuple[str, ...] = SEARCH_SOURCES
    results_per_source: int = 8


@dataclass
class Config:
    library: LibraryConfig = field(default_factory=LibraryConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)

    @property
    def library_path(self) -> Path:
        return self.library.path.expanduser()


def _as_path(value: Any, default: Path) -> Path:
    if value is None or value == "":
        return default
    return Path(str(value)).expanduser()


def _as_int(value: Any, default: int, *, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _as_choice(value: Any, default: str, choices: tuple[str, ...]) -> str:
    text = str(value).strip().lower()
    return text if text in choices else default


def _as_sources(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return SEARCH_SOURCES
    picked = tuple(
        str(item).strip().lower()
        for item in value
        if str(item).strip().lower() in SEARCH_SOURCES
    )
    return picked or SEARCH_SOURCES


def _from_mapping(data: dict[str, Any]) -> Config:
    library_data = data.get("library", {})
    download_data = data.get("download", {})

    # `quality` was the pre-0.1 name for the output format.
    fmt = str(download_data.get("format", download_data.get("quality", "any")))
    fmt = FORMAT_ALIASES.get(fmt.strip().lower(), fmt)

    return Config(
        library=LibraryConfig(
            path=_as_path(library_data.get("path"), DEFAULT_LIBRARY_PATH),
        ),
        download=DownloadConfig(
            format=_as_choice(fmt, "any", AUDIO_FORMATS),
            bitrate=str(download_data.get("bitrate", "192")),
            threads=_as_int(download_data.get("threads", 4), 4, low=1, high=16),
            sources=_as_sources(download_data.get("sources")),
            results_per_source=_as_int(
                download_data.get("results_per_source", 8), 8, low=1, high=25
            ),
        ),
    )


def to_toml(config: Config) -> str:
    sources = ", ".join(f'"{source}"' for source in config.download.sources)
    return (
        "[library]\n"
        f"path = {json.dumps(str(config.library.path))}\n"
        "\n"
        "[download]\n"
        f'# one of: {", ".join(AUDIO_FORMATS)}\n'
        "# preferred source format: fetched as-is when the source offers it,\n"
        "# otherwise the best available stream is used - never re-encoded\n"
        f'format = "{config.download.format}"\n'
        "# only used when re-encoding (the app never re-encodes)\n"
        f'bitrate = "{config.download.bitrate}"\n'
        f"threads = {config.download.threads}\n"
        f"sources = [{sources}]\n"
        f"results_per_source = {config.download.results_per_source}\n"
    )


def default_toml() -> str:
    return to_toml(Config())


def ensure_config(path: Path = CONFIG_PATH) -> Path:
    """Create config directory and a default config.toml if missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(default_toml(), encoding="utf-8")
    return path


def load_config(path: Path | None = None) -> Config:
    """Load config from disk, creating defaults when the file is absent."""
    config_path = path or CONFIG_PATH
    ensure_config(config_path)
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return _from_mapping(data)


def save_config(config: Config, path: Path | None = None) -> Path:
    """Write config back to disk as TOML."""
    config_path = path or CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(to_toml(config), encoding="utf-8")
    return config_path
