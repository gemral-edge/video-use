"""One-command verification of a rendered cut (render_raw.py / render.py output).

Born 2026-11-16: the same five checks were being typed by hand after every render, and one
report ("sync < 1 ms") claimed more than had been measured. This script MEASURES them:

  1. Durations      video track vs audio track vs the EDL (A/V drift)
  2. Audio peak     clipping risk (peak close to 0 dBFS)
  3. Tail           last frames not black AND goodbye audio not cut mid-word
  4. Joint sync     cross-correlates the output audio with the SOURCE audio at sampled joints
                    (offset in ms; 0 = exactly where the EDL says it should be)
  5. Repeat scan    >=6-word runs still occurring 2x+ in the kept text (advisory, needs transcripts)

Exit 1 if any check is a hard FAIL (warnings alone exit 0).

Usage:
  python helpers/verify_cut.py <edit_dir>                         # edl_v2.json + the single CUT_*.mp4
  python helpers/verify_cut.py <edit_dir> --video out.mp4 --edl edl_v2.json --joints 15
"""
from __future__ import annotations
import argparse, glob, json, math, re, subprocess, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_raw import luma  # noqa: E402

SR = 16000
OK, WARN, FAIL = "✅", "⚠️ ", "❌"
results: list[tuple[str, str, str]] = []


def add(level: str, name: str, msg: str) -> None:
    results.append((level, name, msg))
    print(f"{level} {name}: {msg}", flush=True)


def probe(path: str, stream: str, key: str):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries",
                        f"stream={key}", "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(p.stdout.split()[0])
    except (IndexError, ValueError):
        return None


def pcm(path: str, ss: float, t: float) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(ss, 0):.3f}", "-t", f"{t:.3f}", "-i", path,
                        "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(float)


def db(x: np.ndarray) -> float:
    return 20 * math.log10(math.sqrt(float((x ** 2).mean())) / 32768 + 1e-9) if len(x) else -99.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edit_dir", type=Path)
    ap.add_argument("--video", type=Path, default=None, help="rendered file (default: the single CUT_*.mp4 in edit_dir)")
    ap.add_argument("--edl", type=Path, default=None, help="EDL (default: <edit_dir>/edl_v2.json)")
    ap.add_argument("--joints", type=int, default=9, help="how many joints to sample for sync (default 9)")
    ap.add_argument("--fps", type=float, default=30.0, help="fps used when rendering (segment snapping)")
    ap.add_argument("--sync-tol-ms", type=float, default=5.0)
    a = ap.parse_args()

    edit = a.edit_dir.resolve()
    edl_path = a.edl or edit / "edl_v2.json"
    video = a.video
    if video is None:
        cands = sorted(edit.glob("CUT_*.mp4"))
        if len(cands) != 1:
            sys.exit(f"need exactly one CUT_*.mp4 in {edit} (found {len(cands)}); pass --video")
        video = cands[0]
    video = str(video)
    edl = json.loads(Path(edl_path).read_text(encoding="utf-8"))
    ranges, sources = edl["ranges"], edl["sources"]
    seg = [round((r["end"] - r["start"]) * a.fps) / a.fps for r in ranges]
    print(f"verify: {video}\n        {len(ranges)} ranges, EDL {sum(seg):.2f}s\n", flush=True)

    # 1 -- durations
    dv, da = probe(video, "v:0", "duration"), probe(video, "a:0", "duration")
    if dv is None or da is None:
        add(FAIL, "durations", "cannot read video/audio stream duration")
    else:
        drift = abs(dv - da) * 1000
        tol_edl = len(ranges) / a.fps + 0.2
        lvl = OK if drift < 40 and abs(dv - sum(seg)) < tol_edl else (WARN if drift < 80 else FAIL)
        add(lvl, "durations", f"video {dv:.3f}s · audio {da:.3f}s (track gap {drift:.1f} ms) · EDL {sum(seg):.3f}s "
                              f"(diff {abs(dv - sum(seg)):.3f}s, allowed {tol_edl:.2f}s)")

    # 2 -- peak
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", p.stderr)
    if m:
        peak = float(m.group(1))
        add(OK if peak < -0.3 else WARN, "audio peak", f"{peak:.1f} dBFS" + ("" if peak < -0.3 else " — clipping risk"))
    else:
        add(WARN, "audio peak", "could not measure")

    # 3 -- tail
    dur = dv or da or 0
    bad = [t for t in (dur - 1.5, dur - 1.0, dur - 0.5, dur - 0.1) if (luma(video, t) or 255) < 30]
    add(OK if not bad else FAIL, "tail picture", "last 1.5 s not black" if not bad else
        f"BLACK frame(s) at {', '.join(f'{t:.1f}s' for t in bad)} — set freeze_video_at on the last range")
    tail = pcm(video, max(dur - 0.6, 0), 0.6)
    last = db(tail[-int(0.25 * SR):]) if len(tail) else -99
    add(OK if last < -40 else WARN, "tail audio", f"last 0.25 s at {last:.1f} dB" +
        ("" if last < -40 else " — still loud at the very end: goodbye may be cut mid-word, extend the last range"))

    # 4 -- joint sync
    starts, pos = [], 0.0
    for d in seg:
        starts.append(pos)
        pos += d
    n = len(ranges)
    idx = sorted({round(i * (n - 1) / max(a.joints - 1, 1)) for i in range(a.joints)})
    offs, inconclusive = [], 0
    for i in idx:
        r, d = ranges[i], seg[i]
        L = min(3.0, d - 0.3)
        if L < 0.4:
            continue
        probe_at = r["start"] + 0.1                  # source time of the sample
        b0 = max(r["start"] - 0.5, 0.0)
        out = pcm(video, starts[i] + 0.1, L)
        srcw = pcm(sources[r["source"]], b0, L + 1.2)
        if len(out) < SR * 0.4 or len(srcw) < len(out):
            continue
        out = out - out.mean()
        cor = np.correlate(srcw - srcw.mean(), out, mode="valid")
        k = int(np.argmax(cor))
        masked = cor.copy()
        masked[max(k - int(0.01 * SR), 0):k + int(0.01 * SR) + 1] = -np.inf
        if cor[k] <= 0 or masked.max() >= 0.9 * cor[k]:   # tone / silence / repetition: peak is ambiguous
            inconclusive += 1
            continue
        offs.append((i, ((k / SR) - (probe_at - b0)) * 1000))
    if offs:
        worst = max(abs(o) for _, o in offs)
        add(OK if worst <= a.sync_tol_ms else FAIL, "joint sync",
            f"{len(offs)} joints, worst |offset| {worst:.1f} ms (tolerance {a.sync_tol_ms:g}) · " +
            ", ".join(f"r{i}:{o:+.1f}" for i, o in offs))
    else:
        add(WARN, "joint sync", f"no joint measurable ({inconclusive} ambiguous: tone/silence/repetition; "
                                f"the others too short)")
    if offs and inconclusive:
        add(OK, "joint sync note", f"{inconclusive} sampled joint(s) skipped as ambiguous (not counted)")
    add(OK, "picture sync", f"not measured directly: each segment is cut on whole {a.fps:g}-fps frames, "
                            f"so picture offset is <= half a frame (~{500 / a.fps:.0f} ms) per joint and does not accumulate")

    # 5 -- repeat scan (advisory)
    tfiles = [f for f in glob.glob(str(edit / "transcripts" / "*.json")) if not f.endswith("-clean.json")]
    if tfiles:
        from apply_retake_cuts import norm, report_repeats
        words = [w for w in json.load(open(tfiles[0], encoding="utf-8"))["words"] if w.get("type") == "word"]
        kept = []
        for i, r in enumerate(ranges):
            kept += [(norm(w["text"]), i, w["start"]) for w in words
                     if w["start"] >= r["start"] - 0.01 and w["end"] <= r["end"] + 0.01]
        print("\nrepeat-scan (advisory — read each run: retake -> cut, intentional repetition -> keep):")
        report_repeats(kept)
        add(OK, "repeat scan", "printed above (advisory)")
    else:
        add(WARN, "repeat scan", "no transcripts/ in edit dir — skipped")

    fails = sum(1 for lv, *_ in results if lv == FAIL)
    warns = sum(1 for lv, *_ in results if lv == WARN)
    print(f"\n{'FAIL' if fails else 'PASS'} — {fails} fail, {warns} warning(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
