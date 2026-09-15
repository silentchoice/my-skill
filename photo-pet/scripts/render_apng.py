#!/usr/bin/env python3
"""Export alpha-preserving previews from a final Codex v2 atlas; requires Pillow."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from PIL import Image

# Codex v2 timings, matching hatch-pet/references/animation-rows.md.
ROWS = {
    "idle": [280, 110, 110, 140, 140, 320],
    "running-right": [120] * 7 + [220],
    "running-left": [120] * 7 + [220],
    "waving": [140] * 3 + [280],
    "jumping": [140] * 4 + [280],
    "failed": [140] * 7 + [240],
    "waiting": [150] * 5 + [260],
    "running": [120] * 5 + [220],
    "review": [150] * 5 + [280],
}


def timeline(frames, durations):
    """Normalize lossless consecutive-frame coalescing without ignoring timing."""
    result = []
    for frame, duration in zip(frames, durations):
        pixels = frame.convert("RGBA").tobytes()
        if result and result[-1][0] == pixels:
            result[-1][1] += duration
        else:
            result.append([pixels, duration])
    return result


def save_checked(frames, durations, path):
    frames[0].save(path, format="PNG", save_all=True,
                   append_images=frames[1:], duration=durations, loop=0,
                   disposal=0, blend=0, optimize=False)
    decoded, decoded_durations = [], []
    with Image.open(path) as animation:
        for index in range(animation.n_frames):
            animation.seek(index)
            decoded.append(animation.convert("RGBA"))
            decoded_durations.append(animation.info.get("duration", 0))
        loop_ok = animation.info.get("loop") == 0
    identical = timeline(frames, durations) == timeline(decoded, decoded_durations)
    return {"path": str(path), "source_frames": len(frames),
            "encoded_frames": len(decoded), "rgba_and_timing_identical": identical,
            "loop_ok": loop_ok, "ok": identical and loop_ok}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", default="pet")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", args.prefix):
        parser.error("--prefix must be 1-80 ASCII letters, digits, underscores or hyphens")
    source = args.atlas.expanduser().resolve()
    out = args.output_dir.expanduser().resolve()
    with Image.open(source) as image:
        if image.size != (1536, 2288) or image.n_frames != 1:
            parser.error("expected a static 1536x2288 Codex v2 atlas")
        if "A" not in image.getbands() and "transparency" not in image.info:
            parser.error("atlas has no alpha channel; use the final transparent atlas")
        atlas = image.convert("RGBA")
    if atlas.getchannel("A").getextrema()[0] == 255:
        parser.error("atlas is fully opaque; use the final transparent atlas")
    jobs = []
    for row, (state, durations) in enumerate(ROWS.items()):
        cells = [(row, col) for col in range(len(durations))]
        jobs.append((state, durations, cells))
    # 180 ms is a preview cadence; actual pet gaze responds to pointer position.
    jobs.append(("look", [180] * 16, [(r, c) for r in (9, 10) for c in range(8)]))
    prepared = []
    for state, durations, cells in jobs:
        frames = [atlas.crop((c * 192, r * 208, (c + 1) * 192, (r + 1) * 208))
                  for r, c in cells]
        if any(frame.getchannel("A").getbbox() is None for frame in frames):
            parser.error(f"{state} contains an empty used frame")
        destination = out / f"{args.prefix}-{state}.png"
        if destination == source:
            parser.error("output would overwrite the input atlas")
        prepared.append((state, durations, frames, destination))
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for state, durations, frames, destination in prepared:
        results.append({"state": state, **save_checked(frames, durations, destination)})
    report = {"ok": all(item["ok"] for item in results), "format": "APNG",
              "atlas": str(source), "atlas_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "previews": results}
    report_path = out / "preview-alpha-validation.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "previews": len(results), "report": str(report_path)}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
