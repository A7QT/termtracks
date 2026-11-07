"""Create a clean, platform-neutral friend-sharing ZIP using only the stdlib."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
output = root / "dist/termtracks-share.zip"
output.parent.mkdir(parents=True, exist_ok=True)
files = [root / name for name in (
    "START-HERE.md", "README.md", "LICENSE", "pyproject.toml", "install.py"
)]
files.extend(sorted((root / "src").rglob("*.py")))
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, Path("termtracks") / path.relative_to(root))
print(output)
