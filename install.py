"""Install the extracted TermTracks bundle for the current user."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import venv
from pathlib import Path


def add_to_path(bin_dir: Path) -> None:
    if os.name == "nt":
        import ctypes
        import winreg
        from ctypes import wintypes

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                current, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, kind = "", winreg.REG_EXPAND_SZ
            entries = current.split(";")
            if not any(os.path.normcase(os.path.expandvars(p)) ==
                       os.path.normcase(str(bin_dir)) for p in entries):
                value = current.rstrip(";") + (";" if current else "") + str(bin_dir)
                winreg.SetValueEx(key, "Path", 0, kind, value)
        send_message = ctypes.windll.user32.SendMessageTimeoutW
        send_message.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
        ]
        send_message.restype = wintypes.LPARAM
        environment = ctypes.create_unicode_buffer("Environment")
        result = ctypes.c_size_t()
        send_message(
            0xFFFF, 0x001A, 0, ctypes.cast(environment, ctypes.c_void_p).value,
            0x0002, 5000, ctypes.byref(result),
        )
        return

    shell = Path(os.environ.get("SHELL", "/bin/sh")).name
    if shell == "fish":
        profile = Path.home() / ".config/fish/conf.d/termtracks.fish"
        line = "fish_add_path " + shlex.quote(str(bin_dir))
    else:
        name = {"bash": ".bashrc", "zsh": ".zshrc"}.get(shell, ".profile")
        profile = Path.home() / name
        line = "export PATH=" + shlex.quote(str(bin_dir)) + ':"$PATH"'
    previous = profile.read_text(encoding="utf-8") if profile.exists() else ""
    if line not in previous.splitlines():
        profile.parent.mkdir(parents=True, exist_ok=True)
        with profile.open("a", encoding="utf-8") as handle:
            handle.write("\n# TermTracks command\n" + line + "\n")
        print(f"Added the command directory to {profile}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, help="Override the app directory")
    parser.add_argument("--no-path", action="store_true", help="Don't touch shell/PATH")
    args = parser.parse_args()
    # Kept as a friendly runtime guard: install.py is also run standalone from
    # the shared bundle, where nothing else enforces the Python minimum.
    if sys.version_info < (3, 10):  # noqa: UP036
        print("Python 3.10 or newer is required: https://www.python.org/downloads/")
        return 1
    missing = [name for name in ("ffmpeg", "ffplay") if not shutil.which(name)]
    if missing:
        print("Missing programs on PATH: " + ", ".join(missing))
        print("Install FFmpeg (including ffplay), reopen your terminal, then retry.")
        print("See START-HERE.md or https://ffmpeg.org/download.html")
        return 1
    source = Path(__file__).resolve().parent
    if not (source / "pyproject.toml").exists():
        print("Extract the entire ZIP before running the installer.")
        return 1
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        default_dir = base / "TermTracks"
    else:
        default_dir = Path.home() / ".local/share/termtracks"
    install_dir = (args.install_dir or default_dir).expanduser().resolve()
    env_dir = install_dir / "venv"
    scripts = env_dir / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    try:
        print(f"Installing TermTracks into {install_dir} ...")
        venv.EnvBuilder(with_pip=True).create(env_dir)
        subprocess.run([str(python), "-m", "pip", "install", "--upgrade", str(source)],
                       check=True)
        if os.name == "nt":
            bin_dir = scripts
            launcher = scripts / "termtracks.exe"
        else:
            bin_dir = Path.home() / ".local/bin"
            launcher = bin_dir / "termtracks"
            target = scripts / "termtracks"
            bin_dir.mkdir(parents=True, exist_ok=True)
            if launcher.is_symlink() and launcher.resolve() == target.resolve():
                pass
            elif launcher.exists() or launcher.is_symlink():
                raise OSError(f"{launcher} already exists; it was left untouched")
            else:
                launcher.symlink_to(target)
        if not args.no_path:
            add_to_path(bin_dir)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        print("Check internet connection and Python venv/pip support.", file=sys.stderr)
        return 1
    print("Installed! Open a new terminal and type: termtracks")
    print(f"You can also launch directly: {launcher}")
    print("The extracted bundle can now be deleted. Rerun this installer to update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
