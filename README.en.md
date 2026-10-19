# WASDify

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

The first game is **Dungeons of Kremlin / Подземелья Кремля**. The catalog includes
Russian `PKR.EXE` and English `PKE.EXE`. Four patches are implemented:

| Patch | ID | Status |
|---|---|---|
| WASD controls and direct mouse turning | `wasd` | Implemented for RUS/ENG |
| `-r1`: 640×400 instead of 640×480 | `resolution-640x400` | Implemented for RUS/ENG |
| Render optimization | `render-optimization` | RUS/ENG; [RUS profiling-copy measurements](docs/games/dungeons-of-kremlin/benchmark.md), ENG measurements pending |
| Numeric life and charge | `numeric-hud` | RUS/ENG; [two fields with native icons](docs/games/dungeons-of-kremlin/numeric-hud.md) |

The render patch saves two instructions per horizontal textured pixel and remains
an optional improvement. RUS profiling copies gained 6.8–10.5% in median FPS
with `normal/60000`. Further renderer optimization has been removed from the active plan.
[Analysis, checks and limitations](docs/games/dungeons-of-kremlin/render.md).
An incompatible patch blocks the entire selected set. The resolution patch changes one byte after validating
a unique NE code signature; repeat application is a no-op. Both versions were
analyzed and emulated for the resolution patch; its separate visual DOSBox check is pending.
See the [analysis and limits](docs/games/dungeons-of-kremlin/analysis.md).

The WASD patch preserves arrow keys and adds independent direct horizontal
mouse turning, enabled automatically. The user confirmed it works in-game.
Default sensitivity is 0.2 degrees per mouse counter unit; tuning across
computers and DOSBox configurations remains follow-up playtesting.
See [controls and verification](docs/games/dungeons-of-kremlin/wasd.md).

For game performance in **DOSBox Staging 0.83**, first add or update this
section in the configuration file you use, then restart DOSBox:

```ini
[cpu]
core = dynamic
cpu_cycles_protected = max
```

This configuration resolved the user's performance issue. In our profiling
setup, the original PKR.EXE reached about 275 FPS at 640×480, compared with 14
using `normal/60000`. See the [method and limitations](docs/games/dungeons-of-kremlin/benchmark.md).
The WASD and resolution fixes remain independent of renderer optimization.

## Install

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

Output consists of two standalone files requiring no installed Python:

- **`dist\WASDify.exe`** — GUI; double-click to open without a console window.
- **`dist\wasdify-cli.exe`** — CLI, e.g. `.\dist\wasdify-cli.exe --lang en games`.

For this checkout, the full GUI path is `C:\Source\wasdify\dist\WASDify.exe`.
The EXE files can be moved elsewhere. Game metadata, translations and Tcl/Tk are
bundled; `originals/` and `mqa/` are not included.

Building requires Windows, Python 3.11+ with Tcl/Tk, and internet access for the
initial tool installation. The script creates an isolated `build\exe\venv`
using `.venv`'s Python if available, otherwise `py -3`, then installs the package
with the `exe` extra and runs PyInstaller. Intermediate files stay in `build\exe\`;
both `build/` and `dist/` are Git-ignored. The build checks the standalone CLI,
RU/EN resources and GUI entry-point loading automatically.

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
wasdify plan "C:\Games\Kremlin\PKE.EXE" --patch resolution-640x400
wasdify apply "C:\Games\Kremlin\PKE.EXE" --patch resolution-640x400
wasdify gui
python -m wasdify --help
```

`plan` never writes files. `apply` revalidates the set before writing. For an
unknown EXE, `--game dungeons-of-kremlin` supplies the analysis context without
bypassing patch checks. The resolution probe recognizes compatible original
and patched code regardless of filename or whole-file hash. Run the patched
game with `-r1` in DOS/DOSBox; `-r2` remains 640×480.

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
