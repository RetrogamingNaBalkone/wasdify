"""Run isolated hidden DOSBox trials; all game inputs use guest memory only."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import statistics
import struct
import subprocess
import time

from profile_kremlin import Api, ROOT, capture, counters, key, runtime


def wait_for(api, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise TimeoutError("DOSBox did not reach the required game state")


def trial(directory, variant, mode, core, cycles, seconds, port):
    directory.mkdir(parents=True)
    source = ROOT / "mqa/performance" / variant
    exe = source / "PKR.EXE"
    conf = f"""[sdl]
fullscreen = off
windowresolution = 1024x768
[dosbox]
machine = svga_s3
memsize = 32
[cpu]
core = {core}
cputype = pentium
cpu_cycles = {cycles}
cpu_cycles_protected = {cycles}
[webserver]
webserver_enabled = on
webserver_bind_address = 127.0.0.1
webserver_port = {port}
[autoexec]
mount -u c
mount c "{source}"
c:
pkr.exe -r{mode}
exit
"""
    config = directory / "dosbox.conf"
    config.write_text(conf, encoding="ascii")
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    api = Api(port)
    # Refuse to connect to or modify any pre-existing emulator.
    try:
        api.get("dosbox/info")
    except OSError:
        pass
    else:
        raise RuntimeError("Profiling port is already in use")
    with (directory / "stdout.log").open("wb") as out, (directory / "stderr.log").open("wb") as err:
        proc = subprocess.Popen([str(ROOT / "mqa/dosbox/dosbox.exe"), "-conf", str(config), "-noconsole"],
                                cwd=ROOT / "mqa/dosbox", env=env, stdout=out, stderr=err,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_for(api, lambda: json.loads(api.get("cpu/state")))
            mapping = wait_for(api, lambda: runtime(api))
            wait_for(api, lambda: counters(api, mapping["counter_base"])["present_full"]["completed"])
            # Menu/story fades briefly ignore input. Repeat Enter only until
            # the gameplay surface has completed three presentations.
            for _ in range(15):
                time.sleep(1.5)
                state = counters(api, mapping["counter_base"])
                if state["present_region"]["completed"] >= 3:
                    break
                key(api, 0x1c)
            else:
                capture(api, runtime(api), directory / "startup-failed.bmp")
                raise TimeoutError("Game did not leave the menu/story")
            # Resolve again after loading, rather than trusting initial buffer allocation.
            mapping = wait_for(api, lambda: runtime(api))
            width, height = struct.unpack("<HH", api.get("memory/SS/0xc1fc/4"))
            before_hash = capture(api, mapping, directory / "start.bmp", stable=True)
            key(api, 0x4d, held=True)  # Continuous clockwise turn; stationary game skips redraws.
            target = counters(api, mapping["counter_base"])["present_region"]["completed"] + 2
            wait_for(api, lambda: counters(api, mapping["counter_base"])["present_region"]["completed"] >= target)
            start = counters(api, mapping["counter_base"])
            wall_start = time.perf_counter()
            time.sleep(seconds)
            finish = counters(api, mapping["counter_base"])
            wall_elapsed = time.perf_counter() - wall_start
            api.put("SS/0x21a2", b"\x00")
            after_hash = capture(api, mapping, directory / "finish.bmp")
            delta = {name: {field: (finish[name][field]-start[name][field]) %
                           (1 << (64 if field == "ticks" else 32))
                           for field in ("calls", "ticks", "completed")} for name in start}
            frames = delta["present_region"]["completed"] + delta["present_full"]["completed"]
            # TSC follows emulated PIC time, not the host hardware cycle counter.
            tsc_mhz = 166.0 if cycles == "max" else int(cycles)/575.0
            guest_ticks = (finish["present_region"]["last_start"] -
                           start["present_region"]["last_start"]) % (1 << 32)
            result = {"variant": variant, "mode": mode, "width": width, "height": height,
                      "core": core, "cycles": cycles, "wall_seconds": wall_elapsed,
                      "guest_seconds": guest_ticks / (tsc_mhz * 1e6),
                      "frames": frames, "fps": frames/wall_elapsed,
                      "tsc_mhz": tsc_mhz, "delta": delta,
                      "start_hash": before_hash, "finish_hash": after_hash,
                      "stable_start_capture": True,
                      "instrumented_sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}
            if ((variant != "control" and delta["horizontal"]["completed"] == 0)
                    or frames < 5 or before_hash == after_hash):
                raise RuntimeError("Trial did not exercise moving game rendering")
            (directory / "result.json").write_text(json.dumps(result, indent=2))
            return result
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--seconds", type=float, default=6)
    p.add_argument("--core", choices=("normal", "dynamic"), default="normal")
    p.add_argument("--cycles", default="60000")
    p.add_argument("--modes", type=int, nargs="+", default=[2, 3, 4])
    p.add_argument("--port", type=int, default=8096)
    p.add_argument("--variants", nargs="+", choices=("before", "after", "control"), default=["before", "after"])
    args = p.parse_args()
    out = ROOT / "mqa/performance" / datetime.now().strftime("run-%Y%m%d-%H%M%S")
    results = []
    for mode in args.modes:
        for repeat in range(args.repeats):
            # Alternate pair order to reduce warm-up and system-load bias.
            for variant in (args.variants if repeat % 2 == 0 else list(reversed(args.variants))):
                directory = out / f"r{mode}-{repeat+1}-{variant}"
                result = trial(directory, variant, mode, args.core, args.cycles, args.seconds, args.port)
                results.append(result)
                (out / "results.json").write_text(json.dumps(results, indent=2))
                print(json.dumps({"trial": directory.name, "fps": round(result["fps"], 3),
                                  "guest_seconds": round(result["guest_seconds"], 3)}), flush=True)
    for mode in args.modes:
        for variant in args.variants:
            values = [r["fps"] for r in results if r["mode"] == mode and r["variant"] == variant]
            print(mode, variant, "median", statistics.median(values), "range", min(values), max(values))
    print(out, flush=True)


if __name__ == "__main__":
    main()
