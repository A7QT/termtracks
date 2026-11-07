# TermTracks — start here

This bundle works on Linux, macOS, and Windows. Extract the whole ZIP first.
Installation needs an internet connection, Python 3.10+, and FFmpeg with
`ffmpeg` and `ffplay` available on PATH. No administrator access is needed
for TermTracks itself; installing system prerequisites may require it.

Get Python from https://www.python.org/downloads/ (or your Linux package
manager). On Linux, ensure Python's venv/pip support is installed too.
Get FFmpeg for your platform from https://ffmpeg.org/download.html or your
package manager. For Windows builds, add the folder containing `ffmpeg.exe`
and `ffplay.exe` to PATH. Open a fresh terminal after installing prerequisites.

## Install once

Open a terminal in the extracted `termtracks` folder:

| Platform | Command |
| --- | --- |
| Linux / macOS | `python3 install.py` |
| Windows (PowerShell or Command Prompt) | `py install.py` |

On Windows, `python install.py` also works if the `py` launcher is unavailable.
The installer checks FFmpeg, creates a private Python environment, installs
TermTracks and its dependencies, and adds its command directory to your PATH.
On Linux/macOS it updates the startup file for bash, zsh, fish, or sh based on
`SHELL`. On Windows it updates your user PATH. For another shell, add the printed
command directory to PATH manually. `--no-path` skips PATH changes.

**Open a new terminal** (on Windows, close and reopen Windows Terminal), then:

```sh
termtracks
```

No virtual-environment activation is needed. The extracted folder can be deleted
after installation. Settings and downloaded songs are kept separately.

## Use it

- `/`: search YouTube or SoundCloud.
- `enter`: preview the selected result.
- `d`: download the selected result.
- `ctrl+o`: Settings; select Directory to choose the download folder.
- `q`: quit.

The default folder is `~/Music`, including on Windows. In Directory, use
`~/Music/TermTracks`, a full Unix path, or a Windows path such as
`C:\Users\YourName\Music`. Enter saves; Escape cancels. Missing folders are
created; existing songs stay where they are.

## Update or uninstall

To update, extract a newer bundle and rerun its installer.

To uninstall on Linux/macOS, delete `~/.local/bin/termtracks` and
`~/.local/share/termtracks`. You may remove the TermTracks PATH line from your
shell startup file (or `~/.config/fish/conf.d/termtracks.fish`).
On Windows, delete `%LOCALAPPDATA%\TermTracks` and remove its `venv\Scripts`
entry from your user PATH. Custom `--install-dir` installations use that folder
instead. Downloaded songs and `~/.config/termtracks` settings remain intact.
