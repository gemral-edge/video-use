"""Single-pass render that KEEPS THE ORIGINAL AUDIO (no loudnorm, no gain, no resample).

Why (chi Jennie 15/11): render.py compresses each segment to AAC then `-c copy` concats,
leaving an AAC priming gap at every joint = audible "bup". loudnorm's limiter also distorts
sources that already peak near 0 dBFS. This script feeds every range as its own
`-ss/-t` input, joins them with the `concat` FILTER and encodes audio exactly once.

Usage:
  python helpers/render_raw.py <edl_v2.json> -o <out.mp4> [--crf 17] [--preset fast] [--fade-ms 5]

Audio: AAC 320k at the SOURCE sample rate (default 44100). Only a tiny fade (default 5 ms)
at each joint against clicks. Video: every segment length is snapped to whole frames so
audio/video stay locked across ~70 joints.
"""
from __future__ import annotations
import argparse, json, subprocess, sys, tempfile, os
from pathlib import Path

FPS = 30.0


def luma(src, t):
    """Mean luma (0-255) of the frame at source time t; None if unreadable."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{max(t, 0):.3f}", "-i", src, "-frames:v", "1",
                        "-vf", "scale=64:-1,signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                       capture_output=True, text=True)
    for ln in p.stderr.splitlines():
        if "YAVG=" in ln:
            return float(ln.split("YAVG=")[1].split()[0])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edl", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--crf", default="17")
    ap.add_argument("--preset", default="fast")
    ap.add_argument("--fade-ms", type=float, default=5.0)
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("--ar", default="44100")
    a = ap.parse_args()

    edl = json.loads(a.edl.read_text(encoding="utf-8"))
    ranges, sources = edl["ranges"], edl["sources"]
    fade = a.fade_ms / 1000.0
    # Black-tail guard (16/11): an exported CC_ file can go black while she is still saying the
    # goodbye. Warn unless the last range already freezes the picture.
    last = ranges[-1]
    y = luma(sources[last["source"]], last["end"] - 0.1)
    if y is not None and y < 30 and last.get("freeze_video_at") is None:
        print(f"WARNING: source picture is BLACK (luma {y:.0f}) at the end of the last range "
              f"({last['end']:.2f}s) -> the goodbye would play over a black screen. Find the last good "
              f"frame and set \"freeze_video_at\" on the last range (see SKILL.md step 6).", flush=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"]
    parts, labels = [], []
    for i, r in enumerate(ranges):
        d = round((r["end"] - r["start"]) * a.fps) / a.fps
        cmd += ["-ss", f"{r['start']:.3f}", "-t", f"{d + 0.2:.3f}", "-i", sources[r["source"]]]
        # optional: source picture goes black/ends before the audio does (e.g. black tail in the
        # exported CC_ file) -> hold the last good frame. Set "freeze_video_at": <source seconds>.
        fz = r.get("freeze_video_at")
        if fz is not None and fz - r["start"] < d:
            keep = round((fz - r["start"]) * a.fps) / a.fps
            vtrim = f"trim=duration={keep:.5f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={d - keep + 0.1:.5f},trim=duration={d:.5f}"
        else:
            vtrim = f"trim=duration={d:.5f},setpts=PTS-STARTPTS"
        parts.append(
            f"[{i}:v]{vtrim},fps={a.fps:g},setsar=1,format=yuv420p[v{i}];"
            f"[{i}:a]atrim=duration={d:.5f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={fade:.4f},afade=t=out:st={max(d - fade, 0):.5f}:d={fade:.4f}[a{i}]"
        )
        labels.append(f"[v{i}][a{i}]")
    parts.append("".join(labels) + f"concat=n={len(ranges)}:v=1:a=1[v][a]")
    fs = Path(tempfile.gettempdir()) / "render_raw_filter.txt"
    fs.write_text(";\n".join(parts), encoding="utf-8")
    cmd += ["-filter_complex_script", str(fs), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", a.preset, "-crf", a.crf, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "320k", "-ar", a.ar, "-movflags", "+faststart", str(a.out)]
    print(f"{len(ranges)} ranges -> {a.out}", flush=True)
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
