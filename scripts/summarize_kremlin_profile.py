"""Publish numeric profiling summaries without executable bytes or memory dumps."""
import argparse
import json
from pathlib import Path
import statistics
import struct


def summarize(paths):
    groups = {}
    for path in paths:
        for row in json.loads((path / "results.json").read_text()):
            expected = {2: (640, 480), 3: (800, 600), 4: (1024, 768)}[row["mode"]]
            if (row["width"], row["height"]) != expected:
                # Early runs read live SS during an interrupt. Verify effective
                # mode independently from the saved BMP and emulator log; retain
                # the bad auxiliary read in the published numeric evidence.
                matches = [p for p in path.glob("*/result.json") if json.loads(p.read_text()) == row]
                if len(matches) != 1:
                    raise ValueError("Cannot validate a mismatching dimension read")
                directory = matches[0].parent
                actual = struct.unpack_from("<ii", (directory / "start.bmp").read_bytes(), 18)
                log = (directory / "stderr.log").read_text(errors="replace")
                if actual != expected or f"VESA {actual[0]}x{actual[1]} " not in log:
                    raise ValueError("Requested mode, capture and emulator disagree")
                row = dict(row, auxiliary_dimension_read=[row["width"], row["height"]],
                           width=actual[0], height=actual[1])
            key = (row["core"], row["cycles"], row["width"], row["height"], row["variant"])
            groups.setdefault(key, []).append(row)
    result = []
    for (core, cycles, width, height, variant), rows in groups.items():
        fps = [r["fps"] for r in rows]
        clock = lambda r: r["guest_seconds"] * r["tsc_mhz"] * 1e6
        result.append({"core": core, "cycles": cycles, "width": width, "height": height,
                       "variant": variant, "n": len(rows),
                       "fps_median": statistics.median(fps), "fps_min": min(fps), "fps_max": max(fps),
                       "guest_time_percent": {name: statistics.median(
                           r["delta"][name]["ticks"] / clock(r) * 100 for r in rows)
                           for name in rows[0]["delta"]},
                       "start_hashes": sorted({r["start_hash"] for r in rows}),
                       "corrected_auxiliary_reads": [r["auxiliary_dimension_read"] for r in rows
                                                     if "auxiliary_dimension_read" in r],
                       "trials": [{"fps": r["fps"], "frames": r["frames"],
                                   "wall_seconds": r["wall_seconds"], "guest_seconds": r["guest_seconds"],
                                   "horizontal_calls": r["delta"]["horizontal"]["completed"],
                                   "vertical_calls": r["delta"]["vertical"]["completed"],
                                   "bank_calls": r["delta"]["bank"]["completed"]} for r in rows]})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("runs", type=Path, nargs="+")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    result = summarize(args.runs)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for r in result:
        print(f"{r['core']} {r['cycles']} {r['width']}x{r['height']} {r['variant']} "
              f"n={r['n']} {r['fps_median']:.3f} [{r['fps_min']:.3f}, {r['fps_max']:.3f}] "
              f"H={r['guest_time_percent']['horizontal']:.2f}% "
              f"V={r['guest_time_percent']['vertical']:.2f}% "
              f"copy={r['guest_time_percent']['copy_region']:.2f}% "
              f"bank={r['guest_time_percent']['bank']:.2f}%")


if __name__ == "__main__":
    main()
