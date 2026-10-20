"""Build one standalone Windows GUI/CLI executable and check its resources."""
import json
import os
from pathlib import Path
import subprocess
import struct
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
        "--distpath", str(dist), "--workpath", str(work / "pyinstaller"),
        "--specpath", str(work / "spec"),
    ]
    # Collection helpers can resolve an older wasdify installed in the build
    # environment, independently of Analysis.pathex. Always bundle this checkout.
    package = root / "src" / "wasdify"
    for directory in ("data", "locales"):
        common.extend(["--add-data", str(package / directory)+os.pathsep+"wasdify/"+directory])
    for pattern in ("*.asm", "*.inc"):
        for source in sorted((package / "patches").glob(pattern)):
            common.extend(["--add-data", str(source)+os.pathsep+"wasdify/patches"])
    # A Windows GUI subsystem prevents console creation, including the one-file
    # parent process. The entry point restores output for CLI callers.
    subprocess.run(
        [*common, "--name", "WASDify", "--windowed",
         str(root / "scripts" / "entry_exe.py")],
        cwd=root, env=environment, check=True,
    )

    # Run from outside the source tree; no Python installation or source resource
    # paths are used by these processes. Check both translations and the catalog.
    smoke = work / "smoke"
    smoke.mkdir(parents=True, exist_ok=True)
    cli = dist / "WASDify.exe"
    with cli.open("rb") as executable:
        executable.seek(0x3C)
        pe_offset = struct.unpack("<I", executable.read(4))[0]
        executable.seek(pe_offset + 24 + 68)
        if struct.unpack("<H", executable.read(2))[0] != 2:
            raise RuntimeError("The executable must use the Windows GUI subsystem")
    expected = json.loads((root / "src" / "wasdify" / "data" / "games.json").read_text("utf-8"))
    for language in ("ru", "en"):
        for game in expected["games"]:
            completed = subprocess.run(
                [str(cli), "--lang", language, "--json", "patches", "--game", game["id"]],
                cwd=smoke, env=environment, check=True, capture_output=True, text=True, timeout=60,
            )
            if json.loads(completed.stdout)["patches"] != game["patches"]:
                raise RuntimeError("Unexpected game catalog in the executable")
        setup_directory = smoke / ("dos-setup-"+language)
        setup_directory.mkdir(exist_ok=True)
        subprocess.run([str(cli), "setup", str(setup_directory), "--setup-lang", language,
                        "--overwrite", "--json"], cwd=smoke, env=environment,
                       check=True, capture_output=True, text=True, timeout=60)
        payloads = json.loads((package / "data/kremlin_settings.json").read_text("ascii"))
        if (setup_directory / "SETUP.EXE").read_bytes() != bytes.fromhex(payloads["setup"][language]):
            raise RuntimeError("Unexpected DOS setup payload in the executable")
    version = subprocess.run([str(cli), "--version"], cwd=smoke, env=environment,
                             check=True, capture_output=True, text=True, timeout=60)
    if not version.stdout.startswith("WASDify "):
        raise RuntimeError("Missing CLI version output")
    # Check explicit GUI command parsing in the same executable.
    subprocess.run([str(cli), "gui", "--lang", "ru", "--help"],
                   cwd=smoke, env=environment, check=True, timeout=60)
    invalid = subprocess.run([str(cli), "--json", "unknown"], cwd=smoke,
                             env=environment, capture_output=True, text=True, timeout=60)
    if invalid.returncode != 2 or json.loads(invalid.stdout)["issues"][0]["code"] != "invalid_arguments":
        raise RuntimeError("Unexpected CLI error output or exit code")
    # Remove the obsolete output of earlier two-executable builds only after
    # the replacement has passed its checks.
    (dist / "wasdify-cli.exe").unlink(missing_ok=True)
    print("\nBuild and smoke checks passed:", flush=True)
    print(cli, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
