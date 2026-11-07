"""Checks for platform-sensitive settings, clipboard, and installer behavior."""
import importlib.util
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from termtracks import clipboard
from termtracks.config import Config, LibraryConfig, load_config, save_config

spec = importlib.util.spec_from_file_location(
    "installer", Path(__file__).parents[1] / "install.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class PortabilityTests(unittest.TestCase):
    def test_directory_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            config_file = Path(folder) / "config.toml"
            for value in (r'C:\Users\Friend\Music', '/Music/"live"', '~/Música'):
                config = Config(library=LibraryConfig(path=Path(value)))
                with self.subTest(value=value):
                    save_config(config, config_file)
                    loaded = load_config(config_file).library.path
                    self.assertEqual(loaded, Path(value).expanduser())

    def test_platform_clipboards(self):
        for platform, command in (("darwin", "pbpaste"), ("win32", "powershell.exe"),
                                  ("linux", "wl-paste")):
            with (self.subTest(platform=platform),
                  patch.object(clipboard.sys, "platform", platform),
                  patch.object(clipboard, "_run_paste", return_value="song") as paste):
                self.assertEqual(clipboard.read_clipboard(), "song")
                self.assertEqual(paste.call_args.args[0][0], command)

    def test_missing_clipboard_tool(self):
        with patch.object(clipboard.shutil, "which", return_value=None):
            self.assertEqual(clipboard.read_clipboard(), "")

    @unittest.skipIf(os.name == "nt", "POSIX shell startup files")
    def test_shell_path_preserves_contents_and_is_idempotent(self):
        fish_conf = ".config/fish/conf.d/termtracks.fish"
        for shell, profile in (("bash", ".bashrc"), ("zsh", ".zshrc"),
                               ("sh", ".profile"), ("fish", fish_conf)):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                path = home / profile
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# existing settings\n")
                with (patch.object(installer.Path, "home", return_value=home),
                      patch.dict(os.environ, {"SHELL": "/bin/" + shell})):
                    installer.add_to_path(home / "bin with spaces")
                    first = path.read_text()
                    installer.add_to_path(home / "bin with spaces")
                self.assertEqual(first, path.read_text())
                self.assertTrue(first.startswith("# existing settings\n"))
                self.assertEqual(first.count("# TermTracks command"), 1)

    def test_windows_user_path_preserves_existing_entries(self):
        import ctypes
        registry = Mock()
        registry.CreateKey.return_value.__enter__ = Mock(return_value="key")
        registry.CreateKey.return_value.__exit__ = Mock(return_value=False)
        registry.QueryValueEx.return_value = (r"C:\Existing", 2)
        windows_os = types.SimpleNamespace(name="nt", path=os.path, environ=os.environ)
        with (patch.object(installer, "os", windows_os),
              patch.dict("sys.modules", {"winreg": registry}),
              patch.object(ctypes, "windll", Mock(), create=True)):
            installer.add_to_path(Path("TermTracks/Scripts"))
        registry.SetValueEx.assert_called_once_with(
            "key", "Path", 0, 2, r"C:\Existing;" + str(Path("TermTracks/Scripts"))
        )

    def test_missing_ffmpeg_does_not_install(self):
        with (patch.object(installer.sys, "argv", ["install.py"]),
              patch.object(installer.shutil, "which", return_value=None),
              patch.object(installer.venv, "EnvBuilder") as builder):
            self.assertEqual(installer.main(), 1)
            builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
