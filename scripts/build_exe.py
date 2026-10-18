"""Build standalone Windows GUI/CLI executables and smoke-test their resources."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows EXE files must be built on Windows.")
    root = Path(__file__).resolve().parents[1]
    work = root / "build" / "exe"
    dist = root / "dist"
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(work / "cache")
    # Never let a caller's PYTHONPATH add private analysis files to the bundle.
    environment.pop("PYTHONPATH", None)
    common = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--noupx", "--paths", str(root / "src"),
        "--collect-data", "wasdify", "--collect-submodules", "wasdify",
        "--distpath", str(dist), "--workpath", str(work / "pyinstaller"),
        "--specpath", str(work / "spec"),
    ]
    for name, entry, mode in (
        ("wasdify-cli", "entry_cli.py", "--console"),
        ("WASDify", "entry_gui.py", "--windowed"),
    ):
        subprocess.run(
            [*common, "--name", name, mode, str(root / "scripts" / entry)],
            cwd=root, env=environment, check=True,
        )

    # Run from outside the source tree; no Python installation or source resource
    # paths are used by these processes. Check both translations and the catalog.
    smoke = work / "smoke"
    smoke.mkdir(parents=True, exist_ok=True)
    cli = dist / "wasdify-cli.exe"
    expected = json.loads((root / "src" / "wasdify" / "data" / "games.json").read_text("utf-8"))
    for language in ("ru", "en"):
        for game in expected["games"]:
            completed = subprocess.run(
                [str(cli), "--lang", language, "--json", "patches", "--game", game["id"]],
                cwd=smoke, env=environment, check=True, capture_output=True, text=True, timeout=60,
            )
            if json.loads(completed.stdout)["patches"] != game["patches"]:
                raise RuntimeError("Unexpected game catalog in the executable")
    subprocess.run([str(cli), "--version"], cwd=smoke, env=environment, check=True, timeout=60)
    # The GUI entry point must also load its translations without a console.
    subprocess.run([str(dist / "WASDify.exe"), "--lang", "ru", "--help"],
                   cwd=smoke, env=environment, check=True, timeout=60)
    print("\nBuild and smoke checks passed:", flush=True)
    for name in ("WASDify.exe", "wasdify-cli.exe"):
        print(dist / name, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
