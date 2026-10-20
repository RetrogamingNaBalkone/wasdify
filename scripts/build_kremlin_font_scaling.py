"""Reproduce the packaged font-scaling payloads (NASM is development-only)."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nasm", default="nasm")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    profiles = json.loads((root / "src/wasdify/data/kremlin_font_scaling.json").read_text("utf-8"))["profiles"]
    output = root / "build/font-scaling"
    output.mkdir(parents=True, exist_ok=True)
    for profile in profiles:
        path = output / (profile["language"] + ".bin")
        subprocess.run([args.nasm, "-f", "bin", f'-DENG={int(profile["language"] == "en")}',
                        str(root / "src/wasdify/patches/kremlin_font_scaling.asm"), "-o", str(path)], check=True)
        payload = path.read_bytes()
        if payload != bytes.fromhex(profile["regions"][0]["replacement"]):
            raise ValueError("Packaged payload differs from assembled source")
        origin, loader, selector = profile["regions"]
        hook = b"\xe8" + struct.pack("<h", origin["offset"]+240-loader["offset"]-3) + b"\x90"*27
        if hook != bytes.fromhex(loader["replacement"]):
            raise ValueError("Packaged loader hook differs from the metrics entry")
        print(profile["language"], len(payload), hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    main()
