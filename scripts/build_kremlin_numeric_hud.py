"""Reproduce both 16-bit HUD payloads; NASM is development-only."""
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
    profiles = json.loads((root / "src/wasdify/data/kremlin_numeric_hud.json").read_text("utf-8"))["profiles"]
    output = root / "build/numeric-hud"
    output.mkdir(parents=True, exist_ok=True)
    for profile in profiles:
        path = output / (profile["language"] + ".bin")
        subprocess.run([args.nasm, "-f", "bin", f'-DENG={int(profile["language"] == "en")}',
                        str(root / "src/wasdify/patches/kremlin_numeric_hud.asm"), "-o", str(path)], check=True)
        payload = path.read_bytes()
        expected = bytes.fromhex(profile["replacement"]) + b"".join(
            bytes.fromhex(r["replacement"]) for r in profile["regions"])
        if payload != expected:
            raise ValueError("Packaged payload differs from assembled source")
        print(profile["language"], len(payload), hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    main()
