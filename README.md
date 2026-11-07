# TermTracks

A terminal music tool. Search YouTube and SoundCloud, preview results inline,
and save tracks straight into your Music folder with proper tags. No player
UI, no library management — just search and harvest.

## Install

Requires Python 3.10+ and FFmpeg (both `ffmpeg` and `ffplay` on your PATH).

```sh
curl -fsSL https://raw.githubusercontent.com/A7QT/termtracks/main/scripts/install.sh | bash
```

Once it's on PyPI this becomes:

```sh
pipx install termtracks       # recommended
# or: pip install termtracks
```

Then open a new terminal and run:

```sh
termtracks
```

Haven't used `pipx` before? `pipx install pipx` first, or use `pip install`.
On Windows, install FFmpeg from [ffmpeg.org](https://ffmpeg.org/download.html)
and make sure `ffmpeg.exe`/`ffplay.exe` are on PATH.

### Offline install

No pip access? Build a self-contained bundle:

```sh
python3 scripts/package.py    # produces dist/termtracks-share.zip
```

Send the zip to a friend; it contains the app, an installer, and
[START-HERE.md](START-HERE.md) with platform-specific setup steps.

## Usage

| Key | Action |
| --- | --- |
| `/` | Focus the search box |
| `enter` | Search (in the box) / preview (in results) |
| `d` | Download the highlighted result |
| `del` | Delete the highlighted result's saved file |
| `ctrl+o` | Open Settings |
| `esc` | Leave the search box / stop a preview |
| `q` | Quit |

### Previews

Enter or double-click a result to preview it. Only the first 15 seconds are
fetched (played with `ffplay`); further 15-second chunks download only while
you keep listening. Enter on the same row again stops the preview and
downloads the full track; `esc` or a right-click just stops it.

### Where songs go

Songs are saved directly to `~/Music/<Title>.<ext>`, tagged via mutagen.

Press `ctrl+o` to open **Settings** and change the folder: highlight
**Directory**, press `enter`, type a path like `~/Music/TermTracks` or an
absolute path, and press `enter` to save (`esc` cancels). Missing folders are
created automatically. The setting is remembered for future launches; existing
songs are not moved.

On Linux/macOS, a `/Music` shortcut resolves to `~/Music` only if a `Music`
folder already exists in your home folder (matched case-insensitively);
otherwise it is treated as a literal path.

### Configuration

Config lives at `~/.config/termtracks/config.toml`:

- `library.path` — download folder
- `format`, `bitrate` — output settings
- `sources` — which search sources to query
- `results_per_source` — result count

Searches are cached at `~/.config/termtracks/search-cache.json`
(newest 200 kept).

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
ruff check
```

## License

[MIT](LICENSE)
