"""
srt_to_edl.py — Phân tích SRT từ CapCut, detect retakes + gaps, đề xuất EDL strategy.
Usage: python helpers/srt_to_edl.py <video.srt> [--video <video.mp4>] [--out <edit_dir>]
       python helpers/srt_to_edl.py <video.srt> --execute  # sau khi user confirm
"""

import re
import json
import argparse
import sys
from pathlib import Path
from difflib import SequenceMatcher


# ─── Parse SRT ──────────────────────────────────────────────────────────────

def parse_srt(path: Path) -> list[dict]:
    """Parse SRT → list of {idx, start, end, text}."""
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\n\s*\n", text.strip())
    entries = []
    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        try:
            idx = int(lines[0])
        except ValueError:
            continue
        m = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s+-->\s+(\d+):(\d+):(\d+)[,.](\d+)",
            lines[1],
        )
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end   = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        phrase = " ".join(lines[2:])
        entries.append({"idx": idx, "start": start, "end": end, "text": phrase})
    return entries


# ─── Detect retakes (similar consecutive phrases) ───────────────────────────

def similarity(a: str, b: str) -> float:
    a_norm = a.lower().strip()
    b_norm = b.lower().strip()
    return SequenceMatcher(None, a_norm[:80], b_norm[:80]).ratio()


def detect_retakes(entries: list[dict], ratio_threshold=0.75) -> list[dict]:
    """
    Tìm các cặp retake: hai phrase liên tiếp (hoặc cách nhau ≤3 phrase) giống nhau.
    Returns list các "retake event": {first_idx, retry_idx, ratio, gap_s}.
    Chỉ flag retake nếu gap giữa end_A và start_B <= 8s (retake thật xảy ra ngay lập tức).
    """
    retakes = []
    n = len(entries)
    for i in range(n - 1):
        for j in range(i + 1, min(i + 4, n)):  # look-ahead 3
            a, b = entries[i], entries[j]
            # khoảng cách thời gian giữa 2 phrase
            gap = b["start"] - a["end"]
            if gap > 8:  # retake thật không cách nhau quá 8s
                break
            r = similarity(a["text"], b["text"])
            if r >= ratio_threshold:
                retakes.append({
                    "first_idx": i,
                    "retry_idx": j,
                    "first": a,
                    "retry": b,
                    "ratio": round(r, 2),
                    "gap_s": round(gap, 2),
                })
                break  # chỉ lấy retake gần nhất
    return retakes


# ─── Detect large gaps (dead air / scene change) ─────────────────────────────

def detect_gaps(entries: list[dict], min_gap=3.0) -> list[dict]:
    """
    Tìm khoảng trống lớn giữa các phrases (có thể là lúc chị tắt camera, nghỉ, v.v.)
    """
    gaps = []
    for i in range(1, len(entries)):
        gap = entries[i]["start"] - entries[i-1]["end"]
        if gap >= min_gap:
            gaps.append({
                "after_idx": i - 1,
                "before_idx": i,
                "after_end": entries[i-1]["end"],
                "before_start": entries[i]["start"],
                "gap_s": round(gap, 2),
            })
    return gaps


# ─── Build keep-blocks (loại bỏ retakes) ────────────────────────────────────

def build_keep_blocks(entries, retakes, gaps, manual_drop_times=None, manual_keep_times=None):
    """
    Tạo danh sách các block "nên giữ":
    - Ghép các phrase liên tiếp thành đoạn
    - Đánh dấu block bị retake (sẽ drop first attempt)
    - Cắt tại gaps lớn
    - manual_drop_times: list[float] — timestamps (start) cần force-drop, tolerance 0.5s
    """
    n = len(entries)
    drop_idxs = set()

    # Manual drops theo timestamp
    if manual_drop_times:
        for ts in manual_drop_times:
            for i, e in enumerate(entries):
                if abs(e["start"] - ts) <= 0.5:
                    drop_idxs.add(i)
                    break

    # Với mỗi retake: drop first attempt (giữ bản cuối cùng)
    for rt in retakes:
        # drop tất cả phrase từ first_idx đến retry_idx-1
        for k in range(rt["first_idx"], rt["retry_idx"]):
            drop_idxs.add(k)

    # Manual keeps — force giữ lại dù bị detect là retake
    if manual_keep_times:
        for ts in manual_keep_times:
            for i, e in enumerate(entries):
                if abs(e["start"] - ts) <= 0.5:
                    drop_idxs.discard(i)  # bỏ khỏi drop list
                    break

    # Tạo gap boundaries
    gap_after = set(g["after_idx"] for g in gaps)

    blocks = []
    current_start = None
    current_end = None
    current_phrases = []
    block_id = 1

    for i, e in enumerate(entries):
        if i in drop_idxs:
            # flush current block nếu có
            if current_phrases:
                blocks.append({
                    "id": block_id,
                    "start": current_start,
                    "end": current_end,
                    "phrases": current_phrases[:],
                    "drop_reason": None,
                })
                block_id += 1
                current_phrases = []
                current_start = None
                current_end = None
            continue

        if current_start is None:
            current_start = e["start"]

        current_end = e["end"]
        current_phrases.append(e["text"])

        # flush nếu gap lớn sau phrase này
        if i in gap_after:
            if current_phrases:
                blocks.append({
                    "id": block_id,
                    "start": current_start,
                    "end": current_end,
                    "phrases": current_phrases[:],
                    "drop_reason": None,
                })
                block_id += 1
                current_phrases = []
                current_start = None
                current_end = None

    # flush cuối
    if current_phrases:
        blocks.append({
            "id": block_id,
            "start": current_start,
            "end": current_end,
            "phrases": current_phrases[:],
            "drop_reason": None,
        })

    return blocks


# ─── Format time ─────────────────────────────────────────────────────────────

def fmt(s: float) -> str:
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}.{int((s % 1)*1000):03d}"


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("srt", help="Đường dẫn tới file SRT")
    ap.add_argument("--video", help="Đường dẫn video MP4 (để ghi vào EDL)")
    ap.add_argument("--out", help="Thư mục edit/ đầu ra", default=None)
    ap.add_argument("--gap", type=float, default=3.0, help="Ngưỡng gap lớn (giây), default 3s")
    ap.add_argument("--ratio", type=float, default=0.75, help="Ngưỡng similarity retake, default 0.75")
    ap.add_argument("--manual-drop", type=float, nargs="+", metavar="TS",
                    help="Timestamps (giây) cần force-drop, vd: 2689.699")
    ap.add_argument("--manual-keep", type=float, nargs="+", metavar="TS",
                    help="Timestamps (giây) bị detect nhầm là retake nhưng cần giữ, vd: 1463.0")
    ap.add_argument("--execute", action="store_true", help="Tạo EDL JSON (sau khi user confirm)")
    args = ap.parse_args()

    srt_path = Path(args.srt)
    if not srt_path.exists():
        sys.exit(f"❌ Không tìm thấy file: {srt_path}")

    # Default edit dir = cạnh file srt
    edit_dir = Path(args.out) if args.out else srt_path.parent / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"📄 SRT: {srt_path.name}")
    print(f"📁 Edit dir: {edit_dir}")
    print(f"{'='*60}\n")

    # Parse
    entries = parse_srt(srt_path)
    total_dur = entries[-1]["end"] if entries else 0
    print(f"✅ Đã parse: {len(entries)} phrases | Tổng thời lượng: {fmt(total_dur)}\n")

    # Detect gaps
    gaps = detect_gaps(entries, min_gap=args.gap)
    print(f"⏸️  Gaps lớn (≥{args.gap}s): {len(gaps)} chỗ")
    for g in gaps:
        print(f"   {fmt(g['after_end'])} → {fmt(g['before_start'])}  ({g['gap_s']:.1f}s) — sau phrase #{g['after_idx']+1}")

    # Manual drops/keeps — timestamps CỦA VIDEO ĐANG XỬ LÝ, truyền qua CLI.
    # (Trước 2026-08-27 các mốc của 1 video cụ thể — vd 2509.699/1463.0 từ
    # feedback 2026-05-16 — bị hard-code làm mặc định LUÔN ÁP DỤNG cho MỌI
    # video chạy qua script này, kể cả khi không liên quan gì tới nội dung ở
    # đó. Đã gỡ; dùng --manual-drop / --manual-keep khi cần cho video hiện tại.)
    manual_drop_times = list(args.manual_drop) if args.manual_drop else []
    manual_keep_times = list(args.manual_keep) if args.manual_keep else []

    # Detect retakes
    retakes = detect_retakes(entries, ratio_threshold=args.ratio)
    print(f"\n🔁 Retakes phát hiện (ratio≥{args.ratio}): {len(retakes)} lần")
    for rt in retakes:
        print(f"   #{rt['first_idx']+1} → #{rt['retry_idx']+1}  ratio={rt['ratio']}  gap={rt['gap_s']}s")
        print(f"     DROP: [{fmt(rt['first']['start'])}] \"{rt['first']['text'][:60]}\"")
        print(f"     KEEP: [{fmt(rt['retry']['start'])}] \"{rt['retry']['text'][:60]}\"")

    if manual_drop_times:
        print(f"\n✂️  Manual drops: {len(manual_drop_times)} timestamps")
        for ts in manual_drop_times:
            # Tìm phrase gần nhất
            closest = min(entries, key=lambda e: abs(e['start'] - ts))
            if abs(closest['start'] - ts) <= 1.0:
                print(f"   DROP [{fmt(ts)}] \"{closest['text'][:60]}\"")

    # Build blocks
    blocks = build_keep_blocks(entries, retakes, gaps,
                               manual_drop_times=manual_drop_times,
                               manual_keep_times=manual_keep_times)

    # Tính thời lượng sau cắt
    kept_dur = sum(b["end"] - b["start"] for b in blocks)
    dropped_dur = total_dur - kept_dur
    cut_pct = dropped_dur / total_dur * 100 if total_dur else 0

    print(f"\n{'='*60}")
    print(f"📊 THỐNG KÊ SƠ BỘ")
    print(f"{'='*60}")
    print(f"  Thời lượng gốc  : {fmt(total_dur)}")
    print(f"  Thời lượng giữ  : {fmt(kept_dur)}")
    print(f"  Cắt bỏ          : {fmt(dropped_dur)} ({cut_pct:.1f}%)")
    print(f"  Số blocks giữ   : {len(blocks)}")
    print(f"  Gaps lớn (scene): {len(gaps)}")
    print(f"  Retakes loại bỏ : {len(retakes)}")

    print(f"\n{'='*60}")
    print(f"🎬 STRATEGY BLOCKS (preview 20 đầu)")
    print(f"{'='*60}")
    for b in blocks[:20]:
        dur = b["end"] - b["start"]
        preview = " ".join(b["phrases"])[:80]
        print(f"  Block {b['id']:3d} | {fmt(b['start'])} → {fmt(b['end'])} ({dur:.1f}s)")
        print(f"          \"{preview}\"")
    if len(blocks) > 20:
        print(f"  ... và {len(blocks)-20} blocks nữa")

    print(f"\n{'='*60}")
    print(f"⚠️  BORDERLINE (ratio 0.75–0.82) — cần xem lại:")
    print(f"{'='*60}")
    borderline = [rt for rt in retakes if rt["ratio"] < 0.83]
    if borderline:
        for rt in borderline:
            print(f"  ratio={rt['ratio']} | DROP: [{fmt(rt['first']['start'])}] \"{rt['first']['text'][:50]}\"")
            print(f"           KEEP: [{fmt(rt['retry']['start'])}] \"{rt['retry']['text'][:50]}\"")
    else:
        print("  Không có borderline — tất cả retakes đều rõ ràng ✅")

    # Ghi strategy preview
    strategy_path = edit_dir / "strategy_preview.md"
    with open(strategy_path, "w", encoding="utf-8") as f:
        f.write(f"# Strategy Preview — {srt_path.stem}\n\n")
        f.write(f"- Thời lượng gốc: {fmt(total_dur)}\n")
        f.write(f"- Thời lượng sau cắt: {fmt(kept_dur)} ({cut_pct:.1f}% cắt)\n")
        f.write(f"- Retakes loại bỏ: {len(retakes)}\n")
        f.write(f"- Gaps lớn: {len(gaps)}\n")
        f.write(f"- Số blocks: {len(blocks)}\n\n")

        f.write("## Retakes bị xóa\n\n")
        for rt in retakes:
            f.write(f"- [{fmt(rt['first']['start'])}] DROP: \"{rt['first']['text']}\"\n")
            f.write(f"  → [{fmt(rt['retry']['start'])}] KEEP: \"{rt['retry']['text']}\"\n\n")

        f.write("## Blocks giữ lại\n\n")
        for b in blocks:
            dur = b["end"] - b["start"]
            preview = " ".join(b["phrases"])[:100]
            f.write(f"### Block {b['id']} | {fmt(b['start'])} → {fmt(b['end'])} ({dur:.1f}s)\n")
            f.write(f"> {preview}\n\n")

    print(f"\n📝 Strategy preview lưu tại: {strategy_path}")

    # Nếu --execute: tạo EDL JSON
    if args.execute:
        video_path = args.video or str(srt_path.with_suffix(".mp4"))
        source_name = Path(video_path).stem

        edl = {
            "version": 1,
            "sources": {source_name: str(Path(video_path).resolve())},
            "ranges": [],
            "grade": "none",
            "overlays": [],
            "subtitles": None,
            "total_duration_s": round(kept_dur, 2),
        }

        for b in blocks:
            # Pad 50ms trước/sau (Hard Rule 7)
            pad_in  = 0.05
            pad_out = 0.08
            edl["ranges"].append({
                "source": source_name,
                "start": max(0, round(b["start"] - pad_in, 3)),
                "end": round(b["end"] + pad_out, 3),
                "beat": f"block_{b['id']}",
                "quote": " ".join(b["phrases"])[:60],
                "reason": "SRT-driven cut, retakes removed",
            })

        edl_path = edit_dir / "edl.json"
        edl_path.write_text(json.dumps(edl, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ EDL lưu tại: {edl_path}")
        print(f"\n▶️  Bước tiếp theo:")
        print(f"   python helpers/render.py {edl_path} -o {edit_dir}/preview.mp4 --preview")
    else:
        print(f"\n{'='*60}")
        print(f"👆 XEM XÉT STRATEGY TRÊN, SAU ĐÓ CHẠY:")
        print(f"   python helpers/srt_to_edl.py {srt_path} --execute --video \"<đường dẫn mp4>\"")
        print(f"   để tạo EDL và render preview.")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
