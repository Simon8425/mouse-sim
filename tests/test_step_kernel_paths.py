"""Unit tests for FreeCADCmd auto-detection (no FreeCAD required).

freecadcmd_path()'s filesystem probes (glob, is_file, resolve) and the
platform branches (os.name, sys.platform, shutil.which) are mocked, so the
tests are deterministic on any host and never require FreeCAD.
"""

import os
import unittest
from pathlib import Path
from unittest import mock

from mouse_sim.step_kernel import (
    DEFAULT_FREECADCMD,
    FREECADCMD_ENV,
    _windows_freecadcmd_candidates,
    freecadcmd_path,
)


def _win_glob_fake(candidates):
    """Autospec side_effect for Path.glob: only the install pattern matches."""

    def fake(self, pattern):
        if pattern == "FreeCAD*/bin/freecadcmd.exe":
            return [Path(candidate) for candidate in candidates]
        return []

    return fake


class FreecadCmdPathOrderTests(unittest.TestCase):
    def test_env_var_wins_over_glob_and_which(self):
        env_path = "C:/tools/freecad/freecadcmd.exe"
        glob_path = "C:/Program Files/FreeCAD 1.0/bin/freecadcmd.exe"
        which_path = "C:/msys64/usr/bin/freecadcmd.exe"
        with mock.patch.dict(
            os.environ,
            {FREECADCMD_ENV: env_path, "PROGRAMFILES": "C:/Program Files"},
            clear=True,
        ):
            with mock.patch.object(os, "name", "nt"):
                with mock.patch("mouse_sim.step_kernel.sys.platform", "win32"):
                    with mock.patch(
                        "mouse_sim.step_kernel.shutil.which", return_value=which_path
                    ):
                        with mock.patch.object(
                            Path, "glob", autospec=True, side_effect=_win_glob_fake([glob_path])
                        ):
                            with mock.patch.object(Path, "is_file", autospec=True, return_value=True):
                                with mock.patch.object(os, "access", return_value=True):
                                    with mock.patch.object(
                                        Path, "resolve", autospec=True, side_effect=lambda self: self
                                    ):
                                        found = freecadcmd_path()
                                        expected = str(Path(env_path))
        self.assertEqual(str(found), expected)

    def test_env_var_skipped_when_path_does_not_exist(self):
        env_path = "C:/tools/missing/freecadcmd.exe"
        glob_path = "C:/Program Files/FreeCAD 1.0/bin/freecadcmd.exe"
        with mock.patch.dict(
            os.environ,
            {FREECADCMD_ENV: env_path, "PROGRAMFILES": "C:/Program Files"},
            clear=True,
        ):
            with mock.patch.object(os, "name", "nt"):
                with mock.patch("mouse_sim.step_kernel.sys.platform", "win32"):
                    with mock.patch("mouse_sim.step_kernel.shutil.which", return_value=None):
                        with mock.patch.object(
                            Path, "glob", autospec=True, side_effect=_win_glob_fake([glob_path])
                        ):
                            with mock.patch.object(
                                Path, "is_file", autospec=True,
                                side_effect=lambda self: "missing" not in str(self),
                            ):
                                with mock.patch.object(os, "access", return_value=True):
                                    with mock.patch.object(
                                        Path, "resolve", autospec=True, side_effect=lambda self: self
                                    ):
                                        found = freecadcmd_path()
                                        expected = str(Path(glob_path))
        self.assertEqual(str(found), expected)

    def test_windows_glob_hit_prefers_highest_version(self):
        old = "C:/Program Files/FreeCAD 0.20.2/bin/freecadcmd.exe"
        new = "C:/Program Files/FreeCAD 1.0/bin/freecadcmd.exe"
        with mock.patch.dict(os.environ, {"PROGRAMFILES": "C:/Program Files"}, clear=True):
            with mock.patch.object(os, "name", "nt"):
                with mock.patch("mouse_sim.step_kernel.sys.platform", "win32"):
                    with mock.patch("mouse_sim.step_kernel.shutil.which", return_value=None):
                        with mock.patch.object(
                            Path, "glob", autospec=True, side_effect=_win_glob_fake([old, new])
                        ):
                            with mock.patch.object(Path, "is_file", autospec=True, return_value=True):
                                with mock.patch.object(os, "access", return_value=True):
                                    with mock.patch.object(
                                        Path, "resolve", autospec=True, side_effect=lambda self: self
                                    ):
                                        found = freecadcmd_path()
                                        expected = str(Path(new))
        self.assertEqual(str(found), expected)

    def test_windows_candidate_ordering_uses_numeric_version(self):
        names = ["FreeCAD 1.0", "FreeCAD 0.21.2", "FreeCAD 0.9", "FreeCAD Legacy"]
        installs = [
            Path("C:/Program Files") / name / "bin" / "freecadcmd.exe" for name in names
        ]
        with mock.patch.dict(os.environ, {"PROGRAMFILES": "C:/Program Files"}, clear=True):
            with mock.patch.object(Path, "glob", autospec=True, side_effect=_win_glob_fake(installs)):
                ordered = _windows_freecadcmd_candidates()
        self.assertEqual(
            [path.parent.parent.name for path in ordered],
            ["FreeCAD 1.0", "FreeCAD 0.21.2", "FreeCAD 0.9", "FreeCAD Legacy"],
        )

    def test_which_fallback_when_glob_empty(self):
        which_path = "C:/Users/someone/AppData/Local/Programs/FreeCAD 1.0/bin/freecadcmd.exe"
        with mock.patch.dict(os.environ, {"PROGRAMFILES": "C:/Program Files"}, clear=True):
            with mock.patch.object(os, "name", "nt"):
                with mock.patch("mouse_sim.step_kernel.sys.platform", "win32"):
                    with mock.patch(
                        "mouse_sim.step_kernel.shutil.which", return_value=which_path
                    ):
                        with mock.patch.object(Path, "glob", autospec=True, return_value=[]):
                            with mock.patch.object(Path, "is_file", autospec=True, return_value=True):
                                with mock.patch.object(os, "access", return_value=True):
                                    with mock.patch.object(
                                        Path, "resolve", autospec=True, side_effect=lambda self: self
                                    ):
                                        found = freecadcmd_path()
                                        expected = str(Path(which_path))
        self.assertEqual(str(found), expected)

    def test_macos_default_only_checked_on_darwin(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(os, "name", "posix"):
                with mock.patch("mouse_sim.step_kernel.sys.platform", "darwin"):
                    with mock.patch("mouse_sim.step_kernel.shutil.which", return_value=None):
                        with mock.patch.object(Path, "glob", autospec=True, return_value=[]):
                            with mock.patch.object(Path, "is_file", autospec=True, return_value=True):
                                with mock.patch.object(os, "access", return_value=True):
                                    with mock.patch.object(
                                        Path, "resolve", autospec=True, side_effect=lambda self: self
                                    ):
                                        found = freecadcmd_path()
        self.assertEqual(str(found), DEFAULT_FREECADCMD)

    def test_linux_conventional_paths_checked(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(os, "name", "posix"):
                with mock.patch("mouse_sim.step_kernel.sys.platform", "linux"):
                    with mock.patch("mouse_sim.step_kernel.shutil.which", return_value=None):
                        with mock.patch.object(Path, "glob", autospec=True, return_value=[]):
                            with mock.patch.object(Path, "is_file", autospec=True, return_value=True):
                                with mock.patch.object(os, "access", return_value=True):
                                    with mock.patch.object(
                                        Path, "resolve", autospec=True, side_effect=lambda self: self
                                    ):
                                        found = freecadcmd_path()
        self.assertEqual(str(found), "/usr/bin/freecadcmd")

    def test_nothing_found_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(os, "name", "posix"):
                with mock.patch("mouse_sim.step_kernel.sys.platform", "linux"):
                    with mock.patch("mouse_sim.step_kernel.shutil.which", return_value=None):
                        with mock.patch.object(Path, "glob", autospec=True, return_value=[]):
                            with mock.patch.object(Path, "is_file", autospec=True, return_value=False):
                                with mock.patch.object(os, "access", return_value=False):
                                    self.assertIsNone(freecadcmd_path())


if __name__ == "__main__":
    unittest.main()
