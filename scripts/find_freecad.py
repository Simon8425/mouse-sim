"""Diagnose the optional FreeCAD/OCCT STEP backend.

Zero-dependency diagnostic: it reports whatever
``mouse_sim.step_kernel.freecadcmd_path()`` detects (the engine honors
MOUSE_SIM_FREECADCMD, the platform default locations, and PATH) and prints
one-line install guidance per platform.

Exit code: 0 when a usable FreeCADCmd was found, 1 otherwise.

Usage:
    python scripts/find_freecad.py
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_INSTALL_GUIDANCE = {
    "win32": (
        "install FreeCAD from https://www.freecad.org "
        "(auto-detected at C:\\Program Files\\FreeCAD*\\bin\\freecadcmd.exe)"
    ),
    "darwin": (
        "install FreeCAD from https://www.freecad.org "
        "(auto-detected at /Applications/FreeCAD.app)"
    ),
    "linux": (
        "install FreeCAD via your package manager: "
        "apt install freecad / pacman -S freecad / dnf install freecad"
    ),
}


def main():
    try:
        from mouse_sim.step_kernel import freecadcmd_path
    except Exception as exc:
        print("FreeCADCmd: not found")
        print("error: could not import mouse_sim.step_kernel: {}".format(exc))
        print("run this script from the repo checkout (PYTHONPATH=<repo root>)")
        return 1
    path = freecadcmd_path()
    if path:
        print("FreeCADCmd: {}".format(path))
        return 0
    guidance = _INSTALL_GUIDANCE.get(
        sys.platform,
        "install FreeCAD from https://www.freecad.org",
    )
    print("FreeCADCmd: not found")
    print("Install: {}".format(guidance))
    return 1


if __name__ == "__main__":
    sys.exit(main())
