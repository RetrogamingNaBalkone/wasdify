# WASDify: contributor and agent rules

- Read `docs/architecture.md` before changing the core. Use **WASDify** in prose
  and interfaces; lowercase `wasdify` for Python packages, commands and identifiers.
- Python **3.11 is the minimum**. Use only syntax and APIs available there.
  Raising the minimum requires a concrete technical reason and separate user
  approval. CPython reaching end of support does not automatically raise it.
- CLI and GUI must call the same `Service`. Keep Tkinter, printing and translated
  strings out of the core. Import Tkinter lazily so CLI works without Tcl/Tk.
- Add all user-facing strings to both locale catalogs, and game/patch text to
  both metadata languages. Preserve English machine keys and error codes.
- Hashes identify known versions; real patches still validate contextual
  signatures and expected bytes. Never accept a filename, generic MZ header,
  ambiguous match or manual game choice as proof of patch compatibility.
- Stub patches stay explicitly unimplemented. Never write demonstration bytes
  to real game executables or report simulated success as a successful patch.
- Analyze the whole selection before writing. Dependencies are explicit user
  choices. Validate conflicts, cycles, ranges and overlaps. Keep backups on by
  default; revalidate before replacement. File offsets are not memory addresses.
- Add documentation for every game under `docs/games/<game-id>/`, including
  versions, hashes, analysis methods, evidence, hypotheses and patch status.
  Keep implementation notes and public analysis in sync with code.
- Original game files and full dumps belong in `originals/`. Never commit them,
  package them, use them as test fixtures, or modify the user's reference files.
  Only small publishable analysis artifacts belong in `docs/`.
- `mqa/` is also entirely ignored: it holds the user's DOSBox and working game
  copies for analysis. Do not package or commit its contents.
- Tests use synthetic bytes. Run `python -m unittest discover -v` and verify
  installed resource loading outside the repository when changing packaging.
  GUI tests may skip only when Tcl/Tk or a display is unavailable.
- NASM payloads, when introduced, need documented build command, bitness,
  relocation assumptions, provenance and reproducible bytes. No assembler is
  required at application runtime.
