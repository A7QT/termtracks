#!/usr/bin/env bash
# TermTracks instant installer for Linux.
#
# Run it without downloading anything:
#   curl -fsSL https://raw.githubusercontent.com/<YOU>/termtracks/main/scripts/install.sh | bash
#
# Or as a script:  ./install.sh
# Override the install source (defaults to PyPI):
#   TERMTRACKS_SRC="termtracks" bash install.sh                          # PyPI
#   TERMTRACKS_SRC="git+https://github.com/<YOU>/termtracks" bash install.sh

set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
VENV_DIR="${HOME}/.local/share/termtracks/venv"

# The pip specifier to install. Default: install from the GitHub repo.
# Flip to "termtracks" (or remove the override) once it's on PyPI.
SRC="${TERMTRACKS_SRC:-"git+https://github.com/A7QT/termtracks"}"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$1" >&2; exit 1; }

case "$(uname -s)" in
  Linux) ;;
  *) die "This script is for Linux. On macOS/Windows, follow START-HERE.md instead." ;;
esac

# ------------------------------------------------------------ prerequisites

command -v python3 >/dev/null 2>&1 \
  || die "python3 not found. Install Python 3.10+: https://www.python.org/downloads/"

py_ver="$(python3 -c 'import sys; print("%d %d" % sys.version_info[:2])')"
{ [ "${py_ver% *}" -ge 3 ] && [ "${py_ver#* }" -ge 10 ]; } \
  || die "Python ${py_ver} is too old. TermTracks needs Python 3.10+."
log "Python ${py_ver} found"

# FFmpeg is needed for previews (ffplay) and downloads (ffmpeg).
have_ffmpeg() { command -v ffmpeg >/dev/null 2>&1 && command -v ffplay >/dev/null 2>&1; }
if have_ffmpeg; then
  log "FFmpeg found"
elif [ "${TERMTRACKS_AUTOINSTALL_FFMPEG:-1}" = "1" ] && command -v sudo >/dev/null 2>&1; then
  log "FFmpeg missing - installing it via your package manager"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y && sudo apt-get install -y ffmpeg
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y ffmpeg
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm ffmpeg
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y ffmpeg
  elif command -v apk >/dev/null 2>&1; then
    sudo apk add --no-cache ffmpeg
  fi
  if have_ffmpeg; then
    log "FFmpeg installed"
  else
    warn "FFmpeg install did not succeed. Install it from https://ffmpeg.org/download.html"
    warn "Previews and some downloads will not work until ffmpeg and ffplay are on PATH."
  fi
else
  warn "FFmpeg/ffplay not found and auto-install skipped."
  warn "Install it (e.g. 'sudo apt install ffmpeg') for previews and downloads."
fi

# Debian/Ubuntu ships without venv support by default.
if ! python3 -m venv --help >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    log "Installing python3-venv (needed to build the isolated install)"
    sudo apt-get update -y && sudo apt-get install -y python3-venv
  else
    die "python3 venv support is missing. Install the 'python3-venv' package, then rerun."
  fi
fi

mkdir -p "${BIN_DIR}"

# ------------------------------------------------------------ install

# Reuse pipx when present and never used the venv layout before;
# otherwise build a private venv and symlink the entry point.
if [ "${TERMTRACKS_USE_PIPX:-0}" = "1" ]; then
  log "Installing TermTracks via pipx from '${SRC}'"
  pipx install "${SRC}"
  RUN_CMD="termtracks"
  UNINSTALL="pipx uninstall termtracks"
elif [ -d "${VENV_DIR}" ]; then
  log "Updating existing venv install at ${VENV_DIR}"
  "${VENV_DIR}/bin/pip" install --upgrade "${SRC}"
  RUN_CMD="${BIN_DIR}/termtracks"
  UNINSTALL="rm -rf ${BIN_DIR}/termtracks ${VENV_DIR}"
else
  log "Creating isolated venv at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
  log "Installing TermTracks from '${SRC}'"
  "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
  "${VENV_DIR}/bin/pip" install "${SRC}"
  ln -sf "${VENV_DIR}/bin/termtracks" "${BIN_DIR}/termtracks"
  RUN_CMD="${BIN_DIR}/termtracks"
  UNINSTALL="rm -rf ${BIN_DIR}/termtracks ${VENV_DIR}"
fi

# ------------------------------------------------------------ PATH

path_line='export PATH="$HOME/.local/bin:$PATH"'
case ":${PATH}:" in
  *":${BIN_DIR}:"*) log "${BIN_DIR} already on PATH" ;;
  *)
    case "${SHELL##*/}" in
      fish)
        mkdir -p "${HOME}/.config/fish/conf.d"
        echo "fish_add_path ${BIN_DIR}" > "${HOME}/.config/fish/conf.d/termtracks.fish"
        log "Added ${BIN_DIR} to PATH in ~/.config/fish/conf.d/termtracks.fish"
        ;;
      zsh) printf '\n# TermTracks\n%s\n' "${path_line}" >> "${HOME}/.zshrc"
           log "Added ${BIN_DIR} to PATH in ~/.zshrc" ;;
      *)   printf '\n# TermTracks\n%s\n' "${path_line}" >> "${HOME}/.bashrc"
           log "Added ${BIN_DIR} to PATH in ~/.bashrc" ;;
    esac
    log "Open a NEW terminal (or 'source' that file) for PATH to apply."
    ;;
esac

# ------------------------------------------------------------ done

cat <<EOF

Done! TermTracks is installed.

  ${RUN_CMD}

Keys once inside:
  /        search
  enter    search / preview
  d        download
  ctrl+o   settings (change Music folder)
  q        quit

To uninstall later:
  ${UNINSTALL}
EOF
