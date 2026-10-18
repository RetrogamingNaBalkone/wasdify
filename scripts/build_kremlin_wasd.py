"""Rebuild WASDify's own NASM payloads and compare with packaged bytes."""
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
    source = root / "src/wasdify/patches/kremlin_wasd.asm"
    profiles = json.loads((root / "src/wasdify/data/kremlin_wasd.json").read_text("utf-8"))["profiles"]
    output = root / "build/wasd"
    output.mkdir(parents=True, exist_ok=True)
    for profile in profiles:
        for section, index, name in ((1, 0, "keyboard"), (2, 1, "startup"), (3, 5, "cancel"),
                                     (4, 4, "mouse-x"), (5, 6, "angle-helper"), (6, 7, "angle-hook")):
            path = output / f"{name}-{profile['language']}.bin"
            subprocess.run([args.nasm, "-f", "bin", f"-DSECTION={section}",
                            f"-DENG={int(profile['language'] == 'en')}", str(source), "-o", str(path)], check=True)
            payload = path.read_bytes()
            if payload != bytes.fromhex(profile["regions"][index]["replacement"]):
                raise ValueError(f"Packaged payload differs: {path.name}")
            print(path.name, len(payload), hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    main()
