# WASDify

`weapon-selection-fix` for Dungeons of Kremlin removes an unsafe weapon-pointer
read: [Ghidra analysis and validation limits](docs/games/dungeons-of-kremlin/weapons.md).

[Русский](README.md) · [Documentation](docs/README.md)

Targeted binary patches for classic games: modern controls, important bug fixes
and performance improvements that preserve the original experience. WASD and
mouse controls for DOS shooters are the namesake example.

WASDify provides a shared engine, CLI and Tkinter GUI. Users select their game,
executable and individual patches. Known versions are identified by SHA-256;
implemented patches validate contextual signatures and original bytes.
Dependencies must be selected explicitly.

## Status

The application shell, detection, planning, masked signature search, dependency
checks, safe file replacement and optional backups are implemented. Both
interfaces support Russian and English.

The first supported game is **Dungeons of Kremlin / Подземелья Кремля**.
Patch details, supported versions, instructions and verification results are in
the [game documentation](docs/games/dungeons-of-kremlin/README.md) (Russian).

## Install

[DOS game settings](docs/games/dungeons-of-kremlin/settings.md) for Dungeons of
Kremlin are available through `game-settings`; explicitly select `wasd` and
`resolution-640x400` as well. Applying `game-settings` automatically installs
SETUP.EXE beside the game EXE. Its language is detected from the game executable,
independently of the WASDify UI. The game reads KREMLIN.INI directly.

Requires **Python 3.11+**, with Tcl/Tk for the GUI. Windows is the primary tested
platform. Raising the minimum Python version requires a technical justification
and separate approval. There are no third-party runtime dependencies.

From the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\wasdify.exe --lang en games
.\.venv\Scripts\wasdify.exe gui --lang en
```

Use an installed newer interpreter, such as `-3.14`, if preferred. Activation is
optional. `.\.venv\Scripts\wasdify-gui.exe --lang en` also opens the GUI.

## Windows EXE build

From the repository root:

```powershell
.\build.ps1
```

The output is **`dist\WASDify.exe`** with **`dist\WASDify.pak`**, requiring no installed Python:

- Double-click or run without arguments to open the GUI without creating a console window.
- Commands run the CLI: `.\dist\WASDify.exe --lang en games`. Terminal output,
  redirection and exit codes are preserved.
- To choose the GUI language: `.\dist\WASDify.exe gui --lang en`.

In interactive PowerShell, use a pipeline to wait for the CLI and read
`$LASTEXITCODE`: `.\dist\WASDify.exe --lang en games | Out-Host`.

Move the EXE together with `WASDify.pak`, keeping both in the same directory. Game metadata, translations and Tcl/Tk are
bundled; `originals/` and `mqa/` are not included.

Building requires Windows, Python 3.11+ with Tcl/Tk, and internet access for the
initial tool installation. The script creates an isolated `build\exe\venv`
using `.venv`'s Python if available, otherwise `py -3`, then installs the package
with the `exe` extra and runs PyInstaller. Intermediate files stay in `build\exe\`;
both `build/` and `dist/` are Git-ignored. The build checks the standalone CLI,
RU/EN resources, exit codes and GUI command help automatically.
The obsolete `dist\wasdify-cli.exe` from earlier builds is removed after successful checks.

If local PowerShell policy prevents running scripts, use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

This option applies only to that PowerShell process.

## CLI

After activating the venv, or using the full command path:

```text
wasdify --lang en games
wasdify detect "C:\Games\Kremlin\PKE.EXE"
wasdify patches --game dungeons-of-kremlin
wasdify apply "C:\Games\Kremlin\PKE.EXE" --patch wasd
wasdify gui
python -m wasdify --help
```

`plan` never writes files. `apply` revalidates the set before writing. For an
unknown EXE, `--game <game-id>` supplies the analysis context without bypassing
patch compatibility checks. Game-specific launch instructions are in the game documentation.

`--lang ru|en` and `--json` work before or after the subcommand. The default
language follows the system locale: Russian for `ru`, English otherwise.
JSON uses stable English keys, diagnostic codes, and hexadecimal byte strings.
`--json` is not supported for GUI launch. Use `--help` for command syntax.

| Exit code | Meaning |
|---|---|
| 0 | Success, valid plan, or already applied |
| 2 | Invalid arguments |
| 3 | Incompatible/unknown file or invalid selection |
| 4 | Read/write/backup error, or unavailable GUI |
| 5 | Selected placeholder patch |

## GUI and backups

Browse for an EXE to identify it, select patches and press **Check**. Results
include the version, SHA-256 and diagnostics. **Apply** becomes available only
after a valid check. Changing the selection requires another check. Switch
RU/EN directly in the window.

Backups are enabled by default. A real modification creates a unique
`<EXE>.wasdify-<uuid>.bak` beside the original. Disable this explicitly with
`--no-backup` or the GUI checkbox. Backup failure prevents writing; if replacement
fails after backup creation, the verified backup is retained. A no-op does not
create another backup. To restore, close the game and copy the backup over the EXE.

This version writes one main EXE per operation. Close the game and avoid
concurrent edits to the target file while patching.

## Development

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Tests use synthetic binaries only. CI covers Windows on Python 3.11 and the current
stable Python release. NASM is a development tool for reproducible WASD payloads;
it is not needed to run WASDify.

- [Architecture and extension guide](docs/architecture.md) (Russian)
- [Game documentation](docs/games/dungeons-of-kremlin/README.md) (Russian)
- [Agent instructions](AGENTS.md)

Store per-game documentation and analysis in `docs/games/<game-id>/`.
Keep original game files, source material and full dumps in `originals/`, which
is entirely ignored by Git. Game binaries are not distributed with WASDify.
`mqa/` is also entirely ignored and holds DOSBox and working game copies for analysis.

WASDify code is licensed under [MIT](LICENSE).
