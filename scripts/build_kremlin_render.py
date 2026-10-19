"""Reproduce the three relocation-free render edits; NASM is development-only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nasm", default="nasm")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    profiles = json.loads((root / "src/wasdify/data/kremlin_render.json").read_text())["profiles"]
    output = root / "build/render"
    output.mkdir(parents=True, exist_ok=True)
    for section in (1, 2, 3):
        path = output / f"section-{section}.bin"
        subprocess.run([args.nasm, "-f", "bin", f"-DSECTION={section}",
                        str(root / "src/wasdify/patches/kremlin_render.asm"), "-o", str(path)], check=True)
        data = path.read_bytes()
        for profile in profiles:
            assert data == bytes.fromhex(profile["regions"][section - 1]["replacement"])
        print(section, len(data), hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    main()
