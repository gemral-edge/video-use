"""Apply LLM-decided retake cuts to an EDL — Claude-driven, NO external API.

Why this exists (2026-06-20): when an in-loop LLM (Claude) is driving video-use,
it is the dedup judge. It reads the packed transcript + EDL ranges, decides which
spans are failed retakes / false starts / stutters (cross-block AND intra-block),
and writes a small decisions JSON. This helper applies those decisions at the
word level. Do NOT call Gemini/any external LLM for this — the in-loop model is
stronger, has no rate limit, and never caches empty 429 responses.

The original aggressive_cut_vi.py only catches CROSS-block retakes (takes separated
by >=gap pause). INTRA-block retakes ("nói lại ngay" with no >=gap pause) survive.
This helper handles BOTH because Claude reads each range and anchors the exact span.

Decisions JSON (--cuts):
  {
    "drops": [12, 22, 25],                         # remove whole range (false start superseded by next)
    "tails": {"2": "<anchor phrase>", ...},        # cut from first match of phrase to END of range
    "heads": {"41": "<anchor phrase>", ...},       # cut from range START up to (excl) first match
    "spans": {"14": "<anchor phrase>", ...}        # cut exactly the matched word span (internal repeat)
  }
Block index = position in the input EDL's ranges[]. Anchors are matched on the
normalized word sequence (case/punct-insensitive), FIRST occurrence. Make anchors
specific enough to be unique (include a distinguishing word for head/span when the
phrase repeats).

The tool PRINTS match/FAIL/AMBIGUOUS per op and EXITS 1 on any of them:
  FAIL      = anchor not found at all.
  AMBIGUOUS = a tail/head anchor matches >1x in its block. The cut fires on the
              FIRST match, so the extent silently balloons and can delete real
              content — this used to still report "matched OK". A span matching
              >1x is only a note (dropping the first of a repeated pair is a
              documented use).
Exit 0 means "all anchors matched OK and unique". Still verify the resulting text
reads clean at the EDL/text layer BEFORE rendering (cheaper than a 30-min render).

Usage:
  python helpers/apply_retake_cuts.py <edit_dir> --cuts cuts.json
  python helpers/apply_retake_cuts.py <edit_dir> --cuts cuts.json --edl edl.json --out edl_v2.json
Then: python helpers/render.py <edit_dir>/edl_v2.json -o <edit_dir>/final.mp4   (loudnorm -14 LUFS default)
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path

HEAD_PAD, TAIL_PAD = 0.05, 0.08
MIN_PIECE = 0.30


def norm(t: str) -> str:
    return re.sub(r"[.,!?;:\"'\-–]", "", t).lower().strip()


def find_seq(tokens, anchor):
    A = [norm(x) for x in anchor.split() if norm(x)]
    T = [norm(w["text"]) for w in tokens]
    for i in range(len(T) - len(A) + 1):
        if T[i:i + len(A)] == A:
            return i, len(A)
    return -1, len(A)


def count_seq(tokens, anchor):
    """How many times the anchor matches inside this block.

    >1 on a tail/head is DANGEROUS: the cut fires on the FIRST match, so the
    extent silently balloons and can eat real content while the run still
    reports 'matched OK'. >1 on a span is legitimate (documented way to drop
    the first of a repeated pair — see VIDEO_EDITING_EVOLUTION_LOG 2026-06-25).
    """
    A = [norm(x) for x in anchor.split() if norm(x)]
    T = [norm(w["text"]) for w in tokens]
    if not A:
        return 0
    return sum(1 for i in range(len(T) - len(A) + 1) if T[i:i + len(A)] == A)


REPEAT_N = 6


def report_repeats(kept, n=REPEAT_N, cap=25):
    """Advisory: list n-word runs that still occur >=2x in the KEPT text (16/11).

    Catches retakes the reader missed (the usual complaint: "still lots of repeated sentences").
    Intentional anaphora also shows up here — it is a to-read list, never an error (exit code
    is unaffected). Consecutive hits that repeat together are merged into one line.
    """
    from collections import defaultdict
    toks = [k[0] for k in kept]
    pos = defaultdict(list)
    for i in range(len(toks) - n + 1):
        pos[tuple(toks[i:i + n])].append(i)
    rep = {g: p for g, p in pos.items() if len(p) > 1}
    if not rep:
        print(f"repeat-scan: no {n}-word run occurs twice in the kept text")
        return
    covered, lines = set(), []
    for i in range(len(toks) - n + 1):
        g = tuple(toks[i:i + n])
        if g not in rep or i in covered or i != rep[g][0]:
            continue  # report each repeated run once, from its first occurrence
        L = 1
        while (i + L) < len(toks) - n + 1:
            nxt = tuple(toks[i + L:i + L + n])
            if nxt in rep and [q + L for q in rep[g]] == rep[nxt]:
                L += 1
            else:
                break
        for q in rep[g]:
            covered.update(range(q, q + L))
        words = toks[i:i + n + L - 1]
        where = ", ".join(f"[r{kept[q][1]} @{kept[q][2]:.0f}s]" for q in rep[g])
        lines.append(f"   {len(words)}w: {' '.join(words)}  ->  {where}")
    print(f"repeat-scan: {len(lines)} run(s) of >={n} words still occur 2x+ in the kept text "
          f"(read context: retake -> cut it; intentional anaphora -> keep):")
    for ln in lines[:cap]:
        print(ln)
    if len(lines) > cap:
        print(f"   ... +{len(lines) - cap} more")


def main():
    ap = argparse.ArgumentParser(description="Apply Claude-decided retake cuts to an EDL (no external API)")
    ap.add_argument("edit_dir", type=Path, help="<videos_dir>/edit dir (has edl.json + transcripts/)")
    ap.add_argument("--cuts", type=Path, required=True, help="decisions JSON (drops/tails/heads/spans)")
    ap.add_argument("--edl", type=Path, default=None, help="input EDL (default <edit_dir>/edl.json)")
    ap.add_argument("--out", type=Path, default=None, help="output EDL (default <edit_dir>/edl_v2.json)")
    args = ap.parse_args()

    edit = args.edit_dir.resolve()
    edl_path = (args.edl or edit / "edl.json").resolve()
    out_path = (args.out or edit / "edl_v2.json").resolve()
    cuts = json.loads(args.cuts.read_text(encoding="utf-8"))

    drops = set(int(x) for x in cuts.get("drops", []))
    tails = {int(k): v for k, v in cuts.get("tails", {}).items()}
    heads = {int(k): v for k, v in cuts.get("heads", {}).items()}
    spans = {int(k): v for k, v in cuts.get("spans", {}).items()}

    _tpath = next(p for p in (edit / "transcripts").glob("*.json") if not p.stem.endswith("-clean"))
    tr = json.load(open(_tpath, encoding="utf-8"))
    words = [w for w in tr["words"] if w.get("type") == "word"]
    base = json.load(open(edl_path, encoding="utf-8"))

    def win(s, e):
        return [w for w in words if w["end"] > s and w["start"] < e]

    new_ranges, fails, ambigs, kept_tok = [], [], [], []
    for idx, r in enumerate(base["ranges"]):
        if idx in drops:
            print(f"[{idx:02d}] DROP (whole block)")
            continue
        ws = win(r["start"], r["end"])
        keep = [True] * len(ws)
        ops = []
        if idx in tails:
            ops.append(("tail", tails[idx]))
        if idx in heads:
            ops.append(("head", heads[idx]))
        if idx in spans:
            ops.append(("span", spans[idx]))
        for kind, anchor in ops:
            i, L = find_seq(ws, anchor)
            if i < 0:
                fails.append((idx, kind, anchor))
                print(f"[{idx:02d}] !! FAIL {kind}: '{anchor}' NOT FOUND")
                continue
            if kind == "tail":
                for j in range(i, len(ws)):
                    keep[j] = False
            elif kind == "head":
                for j in range(0, i):
                    keep[j] = False
            elif kind == "span":
                for j in range(i, i + L):
                    keep[j] = False
            n_hits = count_seq(ws, anchor)
            if n_hits > 1 and kind in ("tail", "head"):
                ambigs.append((idx, kind, anchor, n_hits))
                print(f"[{idx:02d}] !! AMBIGUOUS {kind}: '{anchor}' matches {n_hits}x in this "
                      f"block — {kind} fires on the FIRST match and may eat real content. "
                      f"Make the anchor unique (add a distinguishing word), or use a span.")
            elif n_hits > 1:
                print(f"[{idx:02d}] note: span '{anchor}' matches {n_hits}x — cutting the FIRST "
                      f"occurrence (documented behaviour)")
            print(f"[{idx:02d}] {kind} OK @word{i} (len {L if kind=='span' else '-'})")

        # contiguous kept runs -> sub-ranges
        runs, j = [], 0
        while j < len(ws):
            if keep[j]:
                k = j
                while k < len(ws) and keep[k]:
                    k += 1
                runs.append((j, k - 1))
                j = k
            else:
                j += 1
        for pi, (a, b) in enumerate(runs):
            ps = r["start"] if a == 0 else max(0.0, ws[a]["start"] - HEAD_PAD)
            pe = r["end"] if b == len(ws) - 1 else min(r["end"], ws[b]["end"] + TAIL_PAD)
            # Clamp padding so it never bleeds into an adjacent word we just cut.
            # At normal speech pace the gap to the next/prev word is often <
            # HEAD_PAD/TAIL_PAD (20-60ms), so the naive pad above can eat 20-60ms
            # of the very retake we're trying to remove — audible as a clipped
            # syllable at the cut. Half-split the real gap instead of the fixed pad.
            if a > 0:
                gap = ws[a]["start"] - ws[a - 1]["end"]
                ps = max(ps, ws[a]["start"] - max(0.0, gap / 2))
            if b < len(ws) - 1:
                gap = ws[b + 1]["start"] - ws[b]["end"]
                pe = min(pe, ws[b]["end"] + max(0.0, gap / 2))
            if pe - ps < MIN_PIECE:
                continue
            nr = dict(r)
            nr["start"], nr["end"] = round(ps, 3), round(pe, 3)
            nr["beat"] = f"{r.get('beat', 'blk')}" + (f"_{pi}" if len(runs) > 1 else "")
            nr["quote"] = " ".join(w["text"] for w in ws[a:b + 1])[:100]
            new_ranges.append(nr)
            kept_tok += [(norm(w["text"]), len(new_ranges) - 1, w["start"]) for w in ws[a:b + 1]]

    base["ranges"] = new_ranges
    out_path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")

    old = json.load(open(edl_path, encoding="utf-8"))["ranges"]
    old_dur = sum(r["end"] - r["start"] for r in old)
    new_dur = sum(r["end"] - r["start"] for r in new_ranges)
    print("=" * 50)
    print(f"ranges: {len(old)} -> {len(new_ranges)}  |  duration {old_dur:.1f}s -> {new_dur:.1f}s (cut {old_dur-new_dur:.1f}s)")
    print(f"wrote {out_path}")
    if fails:
        print(f"!! {len(fails)} ANCHOR FAILURES — fix anchors before render:")
        for idx, k, a in fails:
            print(f"   [{idx}] {k}: {a}")
    if ambigs:
        print(f"!! {len(ambigs)} AMBIGUOUS ANCHORS — tail/head fires on the FIRST match, so the "
              f"cut may have eaten real content. Re-read those blocks before render:")
        for idx, k, a, n in ambigs:
            print(f"   [{idx}] {k}: '{a}' ({n} matches)")
    report_repeats(kept_tok)
    if fails or ambigs:
        raise SystemExit(1)
    print("all anchors matched OK and unique — now verify text reads clean, THEN render")


if __name__ == "__main__":
    main()
