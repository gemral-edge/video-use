"""
Transcribe a video with local Whisper (medium) and convert output to Scribe JSON format
compatible with aggressive_cut_vi.py pipeline.

Fallback for when ELEVENLABS_API_KEY quota is exhausted. Run with a Python that has
`openai-whisper` + `torch` installed (the video-use venv does not ship these by default —
use the system Python instead, e.g. `python.exe` per the canonical path in CLAUDE.md).

Usage:
    python whisper_to_scribe.py <video_path> <edit_dir>
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_audio(video: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  audio extracted: {dest.stat().st_size / 1e6:.1f} MB")


def transcribe_whisper(audio_path: Path, language: str = "vi") -> dict:
    import whisper
    print(f"  loading whisper medium...", flush=True)
    model = whisper.load_model("medium")
    print(f"  transcribing {audio_path.name} ({audio_path.stat().st_size/1e6:.1f} MB)...", flush=True)
    result = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        verbose=False,
        condition_on_previous_text=True,
        no_speech_threshold=0.6,
        logprob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        temperature=0.0,
    )
    return result


def whisper_to_scribe(whisper_result: dict, audio_duration: float) -> dict:
    """Convert Whisper word-level output to ElevenLabs Scribe JSON format."""
    words_scribe = []
    for segment in whisper_result.get("segments", []):
        for w in segment.get("words", []):
            text = w["word"].strip()
            if not text:
                continue
            words_scribe.append({
                "type": "word",
                "text": text,
                "start": round(w["start"], 3),
                "end": round(w["end"], 3),
                "speaker_id": "S0",
                "characters": [{"text": c, "start": w["start"], "end": w["end"]} for c in text],
            })
    return {
        "words": words_scribe,
        "text": whisper_result.get("text", ""),
        "audio_duration_secs": audio_duration,
        "language_code": whisper_result.get("language", "vi"),
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: whisper_to_scribe.py <video_path> <edit_dir>")
        sys.exit(1)

    video = Path(sys.argv[1])
    edit_dir = Path(sys.argv[2])
    tdir = edit_dir / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)

    stem = video.stem
    orig_path = tdir / f"{stem}.json"
    clean_path = tdir / f"{stem}-clean.json"

    if orig_path.exists() and clean_path.exists():
        print(f"  cached: transcripts already exist at {tdir}")
        sys.exit(0)

    print(f"[Whisper] Transcribing: {video.name}", flush=True)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        audio_path = Path(f.name)

    try:
        print("  extract audio...", flush=True)
        extract_audio(video, audio_path)

        # Get audio duration
        import subprocess as sp
        probe = sp.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video)],
            capture_output=True, text=True
        )
        fmt = json.loads(probe.stdout)
        audio_duration = float(fmt.get("format", {}).get("duration", 0))

        result = transcribe_whisper(audio_path, language="vi")

        scribe_json = whisper_to_scribe(result, audio_duration)
        word_count = len([w for w in scribe_json["words"] if w["type"] == "word"])
        print(f"  whisper done: {word_count} words, {audio_duration:.1f}s")

        # Write both orig and clean pointing to same content (Whisper has no audio_event issue)
        orig_path.write_text(json.dumps(scribe_json, ensure_ascii=False, indent=2), encoding="utf-8")
        clean_path.write_text(json.dumps(scribe_json, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  wrote: {orig_path}")
        print(f"  wrote: {clean_path}")

    finally:
        audio_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
