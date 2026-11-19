---
name: video-use
description: Edit any video by conversation. Transcribe, cut, color grade, generate overlay animations, burn subtitles — for talking heads, montages, tutorials, travel, interviews. No presets, no menus. Ask questions, confirm the plan, execute, iterate, persist. Production-correctness rules are hard; everything else is artistic freedom.
---

# Video Use

## Principle

1. **LLM reasons from raw transcript + on-demand visuals.** The only derived artifact that earns its keep is a packed phrase-level transcript (`takes_packed.md`). Everything else — filler tagging, retake detection, shot classification, emphasis scoring — you derive at decision time.
2. **Audio is primary, visuals follow.** Cut candidates come from speech boundaries and silence gaps. Drill into visuals only at decision points.
3. **Ask → confirm → execute → iterate → persist.** Never touch the cut until the user has confirmed the strategy in plain English.
4. **Generalize.** Do not assume what kind of video this is. Look at the material, ask the user, then edit.
5. **Artistic freedom is the default.** Every specific value, preset, font, color, duration, pitch structure, and technique in this document is a *worked example* from one proven video — not a mandate. Read them to understand what's possible and why each worked. Then make your own taste calls based on what the material actually is and what the user actually wants. **The only things you MUST do are in the Hard Rules section below.** Everything else is yours.
6. **Invent freely.** If the material calls for a technique not described here — split-screen, picture-in-picture, lower-third identity cards, reaction cuts, speed ramps, freeze frames, crossfades, match cuts, L-cuts, J-cuts, speed ramps over breath, whatever — build it. The helpers are ffmpeg and PIL. They can do anything the format supports. Do not wait for permission.
7. **Verify your own output before showing it to the user.** If you wouldn't ship it, don't present it.

## Hard Rules (production correctness — non-negotiable)

These are the things where deviation produces silent failures or broken output. They are not taste, they are correctness. Memorize them.

1. **Subtitles are applied LAST in the filter chain**, after every overlay. Otherwise overlays hide captions. Silent failure.
2. **Per-segment extract → lossless `-c copy` concat**, not single-pass filtergraph. Otherwise you double-encode every segment when overlays are added.
3. **30ms audio fades at every segment boundary** (`afade=t=in:st=0:d=0.03,afade=t=out:st={dur-0.03}:d=0.03`). Otherwise audible pops at every cut.
4. **Overlays use `setpts=PTS-STARTPTS+T/TB`** to shift the overlay's frame 0 to its window start. Otherwise you see the middle of the animation during the overlay window.
5. **Master SRT uses output-timeline offsets**: `output_time = word.start - segment_start + segment_offset`. Otherwise captions misalign after segment concat.
6. **Never cut inside a word.** Snap every cut edge to a word boundary from the Scribe transcript.
7. **Pad every cut edge.** Working window: 30–200ms. Scribe timestamps drift 50–100ms — padding absorbs the drift. Tighter for fast-paced, looser for cinematic.
8. **Word-level verbatim ASR only.** Never SRT/phrase mode (loses sub-second gap data). Never normalized fillers (loses editorial signal).
9. **Cache transcripts per source.** Never re-transcribe unless the source file itself changed.
10. **Parallel sub-agents for multiple animations.** Never sequential. Spawn N at once via the `Agent` tool; total wall time ≈ slowest one.
11. **Strategy confirmation before execution.** Never touch the cut until the user has approved the plain-English plan.
12. **All session outputs in `<videos_dir>/edit/`.** `<videos_dir>` is whatever folder the user's source video already lives in — any path, any drive, completely unrelated to this repo. Never require the user's footage to be moved into `video-use/` first, and never write outputs inside the `video-use/` project directory itself. (`video-use/raw-video/` existing as a gitignored convenience staging spot for some past sessions does NOT mean footage has to go there — it's one option among infinite, never a requirement.)

Everything else in this document is a worked example. Deviate whenever the material calls for it.

## Directory layout

The skill lives in `video-use/` (this repo, fixed location). User footage lives wherever the user puts it — a Desktop folder, a different project's folder, another drive, anywhere — with zero requirement that it sit inside or near this repo. `<videos_dir>` always means "the folder the source video is currently in," resolved fresh per-video, per-session. All session outputs go into `<videos_dir>/edit/`, created as a sibling of the source file(s), wherever that turns out to be.

```
<videos_dir>/
├── <source files, untouched>
└── edit/
    ├── project.md               ← memory; appended every session
    ├── takes_packed.md          ← phrase-level transcripts, the LLM's primary reading view
    ├── edl.json                 ← cut decisions
    ├── transcripts/<name>.json  ← cached raw Scribe JSON
    ├── animations/slot_<id>/    ← per-animation source + render + reasoning
    ├── clips_graded/            ← per-segment extracts with grade + fades
    ├── master.srt               ← output-timeline subtitles
    ├── downloads/               ← yt-dlp outputs
    ├── verify/                  ← debug frames / timeline PNGs
    ├── preview.mp4
    └── final.mp4
```

## Setup

First-time install lives in `install.md` (clone, deps, ffmpeg, skill registration, API key). Don't re-run it every session; on cold start just verify:

- `ELEVENLABS_API_KEY` resolves — either in the environment or in `.env` at the video-use repo root. If missing, ask the user to paste one and write it to `.env` (never to the user's `<videos_dir>`).
- `ffmpeg` + `ffprobe` on PATH.
- Python deps installed (`uv sync` or `pip install -e .` inside the repo).
- Node.js + npm available if the session needs HyperFrames or Remotion slots. HyperFrames currently requires Node.js 22+.
- `yt-dlp`, HyperFrames, Remotion, Manim installed only on first use.
- First-use animation setup happens inside the slot directory, never at the video-use repo root. HyperFrames can be invoked with `npx --yes hyperframes ...`; Remotion can be scaffolded with `npx create-video@latest` or installed as a project-local dependency before using its `remotion render` command.
- This skill vendors `skills/manim-video/`. Read its SKILL.md when building a Manim slot.

Helpers (`helpers/transcribe.py`, `helpers/render.py`, etc.) live alongside this SKILL.md. Resolve their paths relative to the directory containing this file — the skill is typically symlinked at `~/.claude/skills/video-use/` or `~/.codex/skills/video-use/`.

## Helpers

- **`transcribe.py <video>`** — single-file Scribe call. `--num-speakers N` optional. Cached.
- **`transcribe_batch.py <videos_dir>`** — 4-worker parallel transcription. Use for multi-take.
- **`pack_transcripts.py --edit-dir <dir>`** — `transcripts/*.json` → `takes_packed.md` (phrase-level, break on silence ≥ 0.5s).
- **`timeline_view.py <video> <start> <end>`** — filmstrip + waveform PNG. On-demand visual drill-down. **Not a scan tool** — use it at decision points, not constantly.
- **`render.py <edl.json> -o <out>`** — per-segment extract → concat → overlays (PTS-shifted) → subtitles LAST. `--preview` for 720p fast. `--build-subtitles` to generate master.srt inline.
- **`grade.py <in> -o <out>`** — ffmpeg filter chain grade. Presets + `--filter '<raw>'` for custom.
- **`aggressive_cut_vi.py <video>`** — full single-source Vietnamese aggressive-cut pipeline (transcribe → cut plan → EDL → render). See "Worked example" below.
- **`apply_retake_cuts.py <edit_dir> --cuts cuts.json`** — apply Claude-decided drops/tails/heads/spans to an EDL at the word level. See "Claude-driven retake removal" below.
- **`whisper_to_scribe.py <video> <edit_dir>`** — local Whisper (medium) fallback when `ELEVENLABS_API_KEY` quota is exhausted; writes Scribe-JSON-compatible transcripts so the rest of the pipeline (aggressive_cut_vi.py, apply_retake_cuts.py) doesn't need to know the difference. Needs `openai-whisper` + `torch` in a Python that has them (not the video-use `.venv` by default).
- **`srt_to_edl.py <video.srt> [--video <mp4>] [--execute]`** — alternate entry point when you only have a CapCut-exported SRT (no Scribe transcript): parses the SRT, detects retake pairs + large gaps, writes `strategy_preview.md` for review, and (with `--execute`) an `edl.json`. Coarser than the Scribe-driven path (SRT has no true word-level timing) — prefer transcribing when possible. `--manual-drop`/`--manual-keep <TS...>` are for THIS video's specific false-positives/negatives found during review, not persistent defaults — don't leave one video's timestamps as a standing default for the next.

For animations, create `<edit>/animations/slot_<id>/` with `Bash` and spawn a sub-agent via the `Agent` tool.

## The process

1. **Inventory.** `ffprobe` every source. `transcribe_batch.py` on the directory. `pack_transcripts.py` to produce `takes_packed.md`. Sample one or two `timeline_view`s for a visual first impression.
2. **Pre-scan for problems.** One pass over `takes_packed.md` to note verbal slips, obvious mis-speaks, or phrasings to avoid. Plain list, feed into the editor brief.
3. **Converse.** Describe what you see in plain English. Ask questions *shaped by the material*. Collect: content type, target length/aspect, aesthetic/brand direction, pacing feel, must-preserve moments, must-cut moments, animation and grade preferences, subtitle needs. Do not use a fixed checklist — the right questions are different every time.
4. **Propose strategy.** 4–8 sentences: shape, take choices, cut direction, animation plan, grade direction, subtitle style, length estimate. **Wait for confirmation.**
5. **Execute.** Produce `edl.json` via the editor sub-agent brief. Drill into `timeline_view` at ambiguous moments. Build animations in parallel sub-agents. Apply grade per-segment. Compose via `render.py`.
6. **Preview.** `render.py --preview`.
7. **Self-eval (before showing the user).** Run `timeline_view` on the **rendered output** (not the sources) at every cut boundary (±1.5s window). Check each image for:
   - Visual discontinuity / flash / jump at the cut
   - Waveform spike at the boundary (audio pop that slipped past the 30ms fade)
   - Subtitle hidden behind an overlay (Rule 1 violation)
   - Overlay misaligned or showing wrong frames (Rule 4 violation)

   Also sample: first 2s, last 2s, and 2–3 mid-points — check grade consistency, subtitle readability, overall coherence. Run `ffprobe` on the output to verify duration matches the EDL expectation.

   If anything fails: fix → re-render → re-eval. **Cap at 3 self-eval passes** — if issues remain after 3, flag them to the user rather than looping forever. Only present the preview once the self-eval passes.
8. **Iterate + persist.** Natural-language feedback, re-plan, re-render. Never re-transcribe. Final render on confirmation. Append to `project.md`.

## Cut craft (techniques)

- **Audio-first.** Candidate cuts from word boundaries and silence gaps.
- **Preserve peaks.** Laughs, punchlines, emphasis beats. Extend past punchlines to include reactions — the laugh IS the beat.
- **Speaker handoffs** benefit from air between utterances. Common values: 400–600ms. Less for fast-paced, more for cinematic. Taste call.
- **Audio events as signals.** `(laughs)`, `(sighs)`, `(applause)` mark beats. Extend past them.
- **Silence gaps are cut candidates.** Silences ≥400ms are usually the cleanest. 150–400ms phrase boundaries are usable with a visual check. <150ms is unsafe (mid-phrase).
- **Example cut padding** (the launch video shipped with this): 50ms before the first kept word, 80ms after the last. Tighter for montage energy, looser for documentary. Stay in the 30–200ms working window (Hard Rule 7).
- **Never reason audio and video independently.** Every cut must work on both tracks.

## The packed transcript (primary reading view)

`pack_transcripts.py` reads all `transcripts/*.json` and produces one markdown file where each take is a list of phrase-level lines, each prefixed with its `[start-end]` time range. Phrases break on any silence ≥ 0.5s OR speaker change. This is the artifact the editor sub-agent reads to pick cuts — it gives word-boundary precision from text alone at 1/10 the tokens of raw JSON.

Example line:
```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

## Editor sub-agent brief (for multi-take selection)

When the task is "pick the best take of each beat across many clips," spawn a dedicated sub-agent with a brief shaped like this. The structure is load-bearing; the pitch-shape example is not.

```
You are editing a <type> video. Pick the best take of each beat and 
assemble them chronologically by beat, not by source clip order.

INPUTS:
  - takes_packed.md (time-annotated phrase-level transcripts of all takes)
  - Product/narrative context: <2 sentences from the user>
  - Speaker(s): <name, role, delivery style note>
  - Expected structure: <pick an archetype or invent one>
  - Verbal slips to avoid: <list from the pre-scan pass>
  - Target runtime: <seconds>

Common structural archetypes (pick, adapt, or invent):
  - Tech launch / demo:   HOOK → PROBLEM → SOLUTION → BENEFIT → EXAMPLE → CTA
  - Tutorial:             INTRO → SETUP → STEPS → GOTCHAS → RECAP
  - Interview:            (QUESTION → ANSWER → FOLLOWUP) repeat
  - Travel / event:       ARRIVAL → HIGHLIGHTS → QUIET MOMENTS → DEPARTURE
  - Documentary:          THESIS → EVIDENCE → COUNTERPOINT → CONCLUSION
  - Music / performance:  INTRO → VERSE → CHORUS → BRIDGE → OUTRO
  - Or invent your own.

RULES:
  - Start/end times must fall on word boundaries from the transcript.
  - Pad cut boundaries (working window 30–200ms).
  - Prefer silences ≥ 400ms as cut targets.
  - Unavoidable slips are kept if no better take exists. Note them in "reason".
  - If over budget, revise: drop a beat or trim tails. Report total and self-correct.

OUTPUT (JSON array, no prose):
  [{"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK",
    "quote": "...", "reason": "..."}, ...]

Return the final EDL and a one-line total runtime check.
```

## Color grade (when requested)

Your job is to **reason about the image**, not apply a preset. Look at a frame (via `timeline_view`), decide what's wrong, adjust one thing, look again.

Mental model is ASC CDL. Per channel: `out = (in * slope + offset) ** power`, then global saturation. `slope` → highlights, `offset` → shadows, `power` → midtones.

**Example filter chains** (`grade.py` has `--list-presets`; use them as starting points or mix your own):

- **`warm_cinematic`** — retro/technical, subtle teal/orange split, desaturated. Shipped in a real launch video. Safe for talking heads.
- **`neutral_punch`** — minimal corrective: contrast bump + gentle S-curve. No hue shifts.
- **`none`** — straight copy. Default when the user hasn't asked.

For anything else — portraiture, nature, product, music video, documentary — invent your own chain. `grade.py --filter '<raw ffmpeg>'` accepts any filter string.

Hard rules: apply **per-segment during extraction** (not post-concat, which re-encodes twice). Never go aggressive without testing skin tones.

## Subtitles (when requested)

Subtitles have three dimensions worth reasoning about: **chunking** (1/2/3/sentence per line), **case** (UPPER/Title/Natural), and **placement** (margin from bottom). The right combo depends on content.

**Worked styles** — pick, adapt, or invent:

**`bold-overlay`** — short-form tech launch, fast-paced social. 2-word chunks, UPPERCASE, break on punctuation, Helvetica 18 Bold, white-on-outline, `MarginV=35`. `render.py` ships with this as `SUB_FORCE_STYLE`.

```
FontName=Helvetica,FontSize=18,Bold=1,
PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,
BorderStyle=1,Outline=2,Shadow=0,
Alignment=2,MarginV=35
```

**`natural-sentence`** (if you invent this mode) — narrative, documentary, education. 4–7 word chunks, sentence case, break on natural pauses, `MarginV=60–80`, larger font for readability, slightly wider max-width. No shipped force_style — design one if you need it.

Invent a third style if neither fits. Hard rules: subtitles LAST (Rule 1), output-timeline offsets (Rule 5).

## Animations (when requested)

Animations match the content and the brand. **Get the palette, font, and visual language from the conversation** — never assume a default. If the user hasn't told you, propose a palette in the strategy phase and wait for confirmation before building anything.

**Tool options:**

Pick the engine per animation slot. Do not default to Remotion just because the animation is web-adjacent.

- **HyperFrames** — Browser-native HTML/CSS/GSAP video compositions: product UI motion, website-to-video or mockup-to-video captures, kinetic typography, landing-page/storyboard promos, data-driven UI states, transparent WebM overlays, and clips that need deterministic frame capture plus HyperFrames lint/validate/render checks. Best when the animation should be authored and verified like a web composition instead of a React component tree.
- **Remotion** — React/CSS compositions with component state, reusable React primitives, or an existing Remotion brand system. Best when the user specifically asks for React/Remotion or when React composition is the simpler authoring model.
- **Manim** — formal diagrams, state machines, equation derivations, graph morphs. Read `skills/manim-video/SKILL.md` and its references for depth.
- **PIL + PNG sequence + ffmpeg** — simple overlay cards: counters, typewriter text, single bar reveals, progressive draws. Fast to iterate, any aesthetic you want. The launch video used this.

For HyperFrames slots, scaffold the slot inside `edit/animations/slot_<id>/` with `npx --yes hyperframes init . --example blank --non-interactive --skip-skills`, build the HTML composition there, run the HyperFrames checks that fit the slot (`lint`, `validate`, and a draft render when practical), then produce the final overlay video with `npx --yes hyperframes render . -o render.mp4` or `--format webm -o render.webm` when alpha is required. Point the EDL overlay `file` at the actual rendered path.

For Remotion slots, keep the Remotion project isolated inside the same slot directory, scaffold with `npx create-video@latest` or install Remotion locally there, render the composition to `render.mp4` with the project-local `remotion render` command, and verify duration and dimensions with `ffprobe`.

None is mandatory. Invent hybrids if useful (e.g., PIL background with a HyperFrames or Remotion layer on top).

**Duration rules of thumb, context-dependent:**

- **Sync-to-narration explanations.** A viewer needs to parse the content at 1×. Rough floor 3s, typical 5–7s for simple cards, 8–14s for complex diagrams. The launch video shipped at 5–7s per simple card.
- **Beat-synced accents** (music video, fast montage). 0.5–2s is fine — they're visual accents, not information. The "readable at 1×" rule becomes *"recognizable at 1×"*, not *"fully parseable."*
- **Hold the final frame ≥ 1s** before the cut (universal).
- **Over voiceover:** total duration ≥ `narration_length + 1s` (universal).
- **Never parallel-reveal independent elements** — the eye can't track two new things at once. One thing, pause, next thing.

**Animation payoff timing (rule for sync-to-narration):** get the payoff word's timestamp. Start the overlay `reveal_duration` seconds earlier so the landing frame coincides with the spoken payoff word. Without this sync the animation feels disconnected.

**Easing** (universal — never `linear`, it looks robotic):

```python
def ease_out_cubic(t):    return 1 - (1 - t) ** 3
def ease_in_out_cubic(t):
    if t < 0.5: return 4 * t ** 3
    return 1 - (-2 * t + 2) ** 3 / 2
```

`ease_out_cubic` for single reveals (slow landing). `ease_in_out_cubic` for continuous draws.

**Typing text anchor trick:** center on the FULL string's width, not the partial-string width — otherwise text slides left during reveal.

**Example palette** (the launch video — one aesthetic among infinite):
- Background `(10, 10, 10)` near-black
- Accent `#FF5A00` / `(255, 90, 0)` orange
- Labels `(110, 110, 110)` dim gray
- Font: Menlo Bold at `/System/Library/Fonts/Menlo.ttc` (index 1)
- ≤ 2 accent colors, ~40% empty space, minimal chrome
- Result: terminal / retro tech feel

This is one style. If the brand is warm and serif, use that. If it's colorful and playful, use that. If the user handed you a style guide, follow it. If they didn't, propose one and confirm.

**Parallel sub-agent brief** — each animation is one sub-agent spawned via the `Agent` tool. Each prompt is self-contained (sub-agents have no parent context). Include:

1. One-sentence goal: *"Build ONE animation: [spec]. Nothing else."*
2. Absolute output path (`<edit>/animations/slot_<id>/render.mp4`)
3. Exact technical spec: resolution, fps, codec, pix_fmt, CRF, duration
4. Style palette as concrete values (RGB tuples, hex, or reference to a design system)
5. Font path with index
6. Frame-by-frame timeline (what happens when, with easing)
7. Anti-list ("no chrome, no extras, no titles unless specified")
8. Code pattern reference (copy helpers inline, don't import across slots)
9. Deliverable checklist (script, render, verify duration via ffprobe, report)
10. **"Do not ask questions. If anything is ambiguous, pick the most obvious interpretation and proceed."**

One sub-agent = one file (unique filenames, parallel agents don't overwrite each other).

## Output spec

Match the source unless the user asked for something specific. Common targets: `1920×1080@24` cinematic, `1920×1080@30` screen content, `1080×1920@30` vertical social, `3840×2160@24` 4K cinema, `1080×1080@30` square. `render.py` defaults the scale to 1080p from any source; pass `--filter` or edit the extract command for other targets. Worth asking the user which delivery format matters.

## EDL format

```json
{
  "version": 1,
  "sources": {"C0103": "/abs/path/C0103.MP4", "C0108": "/abs/path/C0108.MP4"},
  "ranges": [
    {"source": "C0103", "start": 2.42, "end": 6.85,
     "beat": "HOOK", "quote": "...", "reason": "Cleanest delivery, stops before slip at 38.46."},
    {"source": "C0108", "start": 14.30, "end": 28.90,
     "beat": "SOLUTION", "quote": "...", "reason": "Only take without the false start."}
  ],
  "grade": "warm_cinematic",
  "overlays": [
    {"file": "edit/animations/slot_1/render.mp4", "start_in_output": 0.0, "duration": 5.0}
  ],
  "subtitles": "edit/master.srt",
  "total_duration_s": 87.4
}
```

`grade` is a preset name or raw ffmpeg filter. `overlays` are rendered animation clips. `subtitles` is optional and applied LAST.

## Memory — `project.md`

Append one section per session at `<edit>/project.md`:

```markdown
## Session N — YYYY-MM-DD

**Strategy:** one paragraph describing the approach
**Decisions:** take choices, cuts, grades, animations + why
**Reasoning log:** one-line rationale for non-obvious decisions
**Outstanding:** deferred items
```

On startup, read `project.md` if it exists and summarize the last session in one sentence before asking whether to continue.

## Anti-patterns

Things that consistently fail regardless of style:

- **Hierarchical pre-computed codec formats** with USABILITY / tone tags / shot layers. Over-engineering. Derive from the transcript at decision time.
- **Hand-tuned moment-scoring functions.** The LLM picks better than any heuristic you'll write.
- **Whisper SRT / phrase-level output.** Loses sub-second gap data. Always word-level verbatim.
- **Running Whisper locally on CPU.** Slow and it normalizes fillers. Use hosted Scribe.
- **Burning subtitles into base before compositing overlays.** Overlays hide them. (Hard Rule 1.)
- **Single-pass filtergraph when you have overlays.** Double re-encodes. Use per-segment extract → concat.
- **Linear animation easing.** Looks robotic. Always cubic.
- **Hard audio cuts at segment boundaries.** Audible pops. (Hard Rule 3.)
- **Typing text centered on the partial string.** Text slides left as it grows.
- **Sequential sub-agents for multiple animations.** Always parallel.
- **Editing before confirming the strategy.** Never.
- **Re-transcribing cached sources.** Immutable outputs of immutable inputs.
- **Assuming what kind of video it is.** Look first, ask second, edit last.
- **Trusting Scribe's first pass on noisy sources.** When the source has ambient sound (cooking, walking, tapping, music bed), Scribe collapses content into giant `audio_event` blobs with no word-level timing. Always do a second pass with `diarize=false, tag_audio_events=false` and merge.
- **Trusting prefix-only dedupe with strict thresholds.** Comparing only the first 40 chars at ratio≥0.75 plus a "next-is-1.5×-longer" gate misses partial-restate retakes (false start ~ same length as the good take, or false start opens with different filler). Broaden to 80 chars at ratio≥0.65, drop the shorter side regardless of which is longer. For ambiguous pairs and intra-block retakes, the in-loop LLM decides — see "Claude-driven retake removal".
- **Delegating semantic dedup to an external LLM (Gemini) when an in-loop LLM is driving.** You are the judge — read the transcript and decide. An external Flash pass is weaker, rate-limited, and (incident 2026-06-20) silently returned "0 cuts" after caching 69/82 empty 429 responses. Reserve `--llm-review` for standalone/headless runs only.
- **Rendering before verifying at the text layer.** A render is ~30 min; reading the new EDL's transitions as text is ~5s and catches broken/cut-off sentences before you burn the render. Verify EDL/text first, pixels second.

## Jennie YouTube standard run (CC_*.mp4 talking head) — chạy nguyên luồng, không hỏi

Trigger: chị đưa `CC_<TÊN VIDEO>.mp4` + yêu cầu cắt pause ≥1s / filler / retake + link kịch bản. Đây là standing directive → bỏ qua Hard Rule 11 (không trình strategy), chạy thẳng:

1. **Key**: thứ tự `ELEVENLABS_API_KEY` env → kho xoay tua `helpers/pick_elevenlabs_key.py` (aggressive_cut_vi tự gọi khi chưa có cache transcript, cần ≈1,15 credit/giây nguồn) → `.env`. Xem credit: `pick_elevenlabs_key.py --list`. Nếu chị dán key trong chat → chỉ `export` trong lệnh, KHÔNG ghi vào file nào, cuối báo chị đổi key. (11/11: `.env` key còn 1.655 credit, cần 2.058 → chết quota trong khi EL-02 còn 10.000 — đã nối kho vào pipeline.)
1b. **Đường dẫn dài (tên thư mục = tên video dài)**: thư mục + file vượt 260 ký tự → `ls`/`cp`/ffmpeg báo "No such file". Lấy tên ngắn 8.3 (`cmd /c dir /x`, hoặc đường dẫn chị đưa dạng `DBOTML~3\CC_DBO~1.MP4`) để chạy pipeline, và **thay `sources` trong `edl_v2.json` bằng đường dẫn 8.3 trước khi render**. Đổi tên/copy file có tên tiếng Việt dài → dùng Python `os.replace`/`shutil.copyfile` với đường dẫn Windows (`J:\...`, KHÔNG `/j/...` kiểu Git Bash — Python không hiểu).
2. `aggressive_cut_vi.py <video> --no-render` (gap 1.0, LLM review OFF).
3. Dump text từng range + liệt kê từ KHÔNG nằm trong range nào (xác nhận không rơi mất nội dung thật). Đọc **kịch bản làm oracle**: câu kịch bản chỉ có 1 lần mà transcript nói 2 lần = retake.
4. `cuts.json` → `apply_retake_cuts.py` (exit 0 bắt buộc) → dump đầu/cuối mỗi range đọc mối nối.
5. **Câu chào cuối**: render.py chỉ đệm ~80ms sau từ cuối → luôn nới `ranges[-1].end` thêm ~0.7s (không vượt duration nguồn). Lỗi cũ: mất "hẹn gặp lại".
6. **ÂM THANH GIỮ NGUYÊN GỐC (chị Jennie 15/11 — bản 13/11 bị "rè rè bụp bụp")**: KHÔNG loudnorm, KHÔNG tăng/giảm độ to, KHÔNG đổi 44.1→48 kHz. Gốc: nguồn CC_*.mp4 thường đã ~-14 LUFS và đỉnh chạm +0,6 dBFS → bộ chặn đỉnh của loudnorm làm rè; còn `render.py` nén AAC từng đoạn rồi `-c copy` nối → mỗi mối nối có khe đệm AAC = tiếng "bụp". Render 1 lượt: mỗi đoạn là 1 input `-ss/-t` → `concat` filter → nén âm thanh đúng 1 lần (AAC 320k, giữ tần số gốc), chỉ fade 5ms ở mối nối chống click. Script: `helpers/render_raw.py <edl_v2.json> -o <out.mp4>` (CHẠY ĐƯỢC, 16/11; ~10 phút cho 16 phút output). **Đuôi đen ở nguồn (16/11)**: file CC_ có thể đen hình từ giây cuối trong khi tiếng còn nói câu chào — sau khi nới câu chào cuối, đo `signalstats YAVG` các khung cuối của nguồn (đen ≈15, bình thường ≈105); nếu đen thì đặt `"freeze_video_at": <giây nguồn khung cuối còn thấy hình>` vào range cuối của EDL để giữ khung đó đến hết tiếng. Render nền (`run_in_background`, ~30 phút cho 30 phút nguồn).
   **Rè/bụp CÓ SẴN trong file máy quay (15/11, đo MVI_3889)**: máy thu quá to → đỉnh vượt trần 1,0 ở cú bật đầu chữ + tiếng chép môi trong khoảng lặng. Cắt ghép không gỡ được. Trước khi kết luận "lỗi do cắt", so file máy quay `MVI_*` với `CC_*` cùng mốc (bandpower 10ms + vẽ sóng). Gửi chị 25s nghe thử A/B trước khi áp cả video. Chuỗi đã thử (chị chọn bản cuối): `highpass=f=80,adeclip,adeclick,alimiter=limit=0.89:level=disabled` + hạ −25 dB tiếng động trong khoảng lặng (dò điểm phát âm thật theo dải 300–3000 Hz, chừa 12ms) + làm mềm chữ đầu sau khoảng lặng (lên dần 45ms từ 30%). Chữ vỡ tiếng nặng mà vẫn bụp → chị cho cắt bỏ hẳn chữ đó (span cut). Verify: ffprobe video vs audio duration lệch <0.1s (A/V sync), loudnorm summary Integrated ≈ -14 LUFS, ebur128 4s cuối còn tiếng nói.
6b. **KIỂM BẰNG SỐ trước khi giao (16/11)**: `python helpers/verify_cut.py <edit_dir>` — 1 lệnh đo: độ dài hình/tiếng/EDL · đỉnh âm lượng · hình khung cuối có đen không + tiếng cuối có bị cụt không · lệch tiếng tại các mối nối (cross-correlation với tiếng nguồn, mốc ≤5 ms) · cụm lặp còn sót. Thoát 1 = có lỗi cứng, KHÔNG giao. Báo cáo cho chị chỉ được nêu số script in ra (đừng khai "khớp" bằng số khác đã đo: bài 16/11 báo "<1 ms" chỉ là chênh tổng độ dài). Hình (picture sync) script không đo trực tiếp, chỉ suy ra từ việc cắt theo khung — nói đúng như vậy.
7. **Đặt tên output**: `edit/CUT_<TÊN VIDEO>.mp4` (bỏ tiền tố `CC_`), KHÔNG để `final.mp4`. `base.mp4` là nháp (dọn ở bước 10 sau khi đã giao Drive).
8. **Giao lên Drive** (jenniesub1): thư mục `J:\My Drive\Social Media Jennie\Footage Tổng Hợp\YOUTUBE\2026\<TÊN VIDEO>\` (folder 2026 id `1u0GDjEGW2e0RjiLXWNsTxrpqXHXMscrt`). Thiếu ổ J: → bật `GoogleDriveFS.exe` trong thư mục phiên bản số (vd `C:\Program Files\Google\Drive File Stream\123.0.1.0\`, KHÔNG phải thư mục `Drivers`), chờ ~40s. Tạo thư mục tên video rồi `cp` file vào. **Tên thư mục/file trên Drive tối đa 255 byte UTF-8** (13/11: tên 288 byte → `WinError 87`) — tên dài thì cắt ở dấu phẩy đầu tiên cho vừa, file đặt `CUT_<tên đã cắt>.mp4`. Drive MCP `create_file` KHÔNG upload nổi file video trăm MB (base64) — chỉ dùng nó tạo folder/kiểm tra.
8b. **Ghi đè bản cũ trên Drive (19/11)**: `shutil.copyfile` đè lên file đã có trên `J:` thì `os.path.getsize` đọc ngay có thể thấy size DỞ (781.189.120 so với 781.702.759) và `search_files` còn trả bản CŨ (fileSize + modifiedTime cũ) khoảng 2–3 phút. Chưa thấy `fileSize` trên cloud khớp file máy + `modifiedTime` mới = CHƯA giao, đừng báo xong. Chờ bằng lệnh nền (`run_in_background`), không `Start-Sleep` dài trong lệnh chính. Link file giữ nguyên (Drive đổi nội dung tại chỗ).
9. **Lấy LINK Drive và gửi chị (BẮT BUỘC — chị nhắc 20/10 vì báo xong mà chỉ ghi đường dẫn `J:\`):** sau `cp`, dùng Drive MCP `search_files`: `title contains '<TÊN VIDEO>' and parentId = '1u0GDjEGW2e0RjiLXWNsTxrpqXHXMscrt'` → id thư mục; rồi `parentId = '<id thư mục>'` → file; lấy `viewUrl`. Báo cáo cuối PHẢI có **link video + link thư mục** (https://drive.google.com/...) và đối chiếu `fileSize` trên Drive = size file trên máy. Đường dẫn `J:\` chỉ là phụ. Chưa thấy file trong `search_files` thì chờ đồng bộ vài chục giây rồi tìm lại; chưa có link = chưa được báo xong.
10. **Dọn `edit/` sau khi giao:** xoá `base.mp4`, `clips_graded/`, `*.prenorm.mp4`, file dump/log tạm (`ranges.txt`, `render_log.txt`); GIỮ `CUT_*.mp4`, `edl.json`, `edl_v2.json`, `cuts.json`, `transcripts/` (cache Scribe — Hard Rule 9).

Retake patterns gặp ở video này (2026-10-04, 31.8→17.2 phút): đuôi câu kết `??`/`--`/`,` rồi khối sau nói lại cả câu → `tails`; khối chỉ lặp lại vế cuối của khối trước ("Bạn tự mãn, phán xét thiên hạ.") → `drops`; hai lần nói ví dụ (iPhone) → cắt tail lần đầu, giữ lần sau.

## Worked example: Vietnamese single-source aggressive cut

For a single talking-head source where the user wants pure cleanup (cut pauses ≥1s, fillers, false starts, repeats), use `helpers/aggressive_cut_vi.py`. It bakes in:

- **Dual transcribe + hybrid merge** (handles Scribe's collapse-into-audio-event bug)
- **Broken token detection**: any word with `end - start > 5.0` is a Scribe artifact — fall back to the original transcript's words for that span
- **Slate auto-detect**: drop everything before first hybrid word (clean transcript naturally skips slate)
- **Aggressive cuts at gap ≥1s** with surgical trim of trailing `--` cutoffs and leading fillers
- **Two-tier dedupe**:
  - Tier B (rule): SequenceMatcher ratio ≥ 0.65 on first 80 chars; drop the SHORTER block of the pair when `min(d1,d2) < 10s`. No "next-is-longer" gate. Deterministic, no API.
  - Tier C (LLM): **DEFAULT OFF (changed 2026-06-20).** Opt-in `--llm-review` only for standalone/headless runs (cron, batch) with no in-loop LLM. When Claude (or any in-loop LLM) drives, do the semantic pass yourself — see **"Claude-driven retake removal"** below. Do NOT call Gemini.
  - Limitation of aggressive_cut alone: catches only CROSS-block retakes (takes separated by ≥gap pause). Within-block stutter+restart ("nói lại ngay", no ≥gap pause) joins into one string and survives. The Claude-driven pass below handles these.

Helpers:
- `helpers/aggressive_cut_vi.py <video>` — full pipeline, no questions, sane defaults (1080p, no subs, no grade). LLM review OFF by default.
- `helpers/aggressive_cut_vi.py <video> --llm-review` — (standalone/headless ONLY) opt-in Gemini cross-block review.
- `helpers/apply_retake_cuts.py <edit_dir> --cuts cuts.json` — apply Claude-decided retake cuts (cross + intra block) to an EDL at the word level. No external API.
- Filler regex: `^(ờ|ừ|ơ|à|ờm|ừm|èm|hờ|ầm|uhm|umm)[.,!?-]*$`

### Claude-driven retake removal (PREFERRED when an in-loop LLM is driving)

**You (the in-loop LLM) are the dedup judge. Never delegate semantic retake decisions to Gemini when you can read the transcript yourself** — you are stronger than Flash, have no rate limit, and never cache empty 429 responses. (Cost incident 2026-06-20: a per-range Gemini pass hit 429 on 69/82 calls, cached the empties, and silently returned "0 cuts" — Claude reading the transcript directly fixed it in one pass.)

Workflow after `aggressive_cut_vi.py` produces `edl.json`:

1. **Dump ranges as readable text** (one line per range: `[idx] start-end (Ns): full text`). Read all of them.
2. **Decide cuts yourself**, distinguishing real retakes from intentional rhetoric. **⭐ Rule of the cut (Jennie 19/11 — she corrected two over-cuts in one session): cut ONLY a take she ABANDONED mid-sentence (trailing `...`/`--`, a stub like "Có bạn khác,") and then restarted, OR a sentence she said TWICE (drop the first = the flubbed one, keep the redo). A complete sentence that occurs ONCE stays — even when a later sentence says a similar thing in other words, and even inside a longer block whose only bad part is a trailing abandoned clause (use `tails` on that clause, never `drops` on the whole block). Do not trim an in-sentence gloss either ("một lớp kính màu…, một cái lớp filter…" is her elaborating, not a retake). Similar meaning ≠ repeat; "I would have said it once" is not a reason to cut.** `apply_retake_cuts.py` prints a `cut-scan` for this: every cut run holding a finished sentence with <34% word-triple overlap with the kept text — each one is a candidate over-cut, confirm it was abandoned or put it back.
   - **Cut**: false starts, cutoffs (`--`), and "say-it-again" repeats where the next take is fuller/cleaner. A short block fully superseded by the next → drop whole.
   - **KEEP**: intentional anaphora / parallelism ("có người vì X… có người vì Y", "không nằm ở chỗ X mà ở chỗ Y"). A verbatim 8–10-word repeat is almost always a retake; a connective phrase reused across different sentences is not.
   - **Specific failure patterns that survive rule-based dedupe** (found 2026-08-13, 34.7min single-source cut — 8 drops + 2 heads + 1 tail + 1 span needed on top of the rule-based pass):
     - Cutoff fragments ending in `...` (not `--`) — the pipeline's cutoff-trim only strips trailing `--`, so a false start that trails off with `...` survives as its own kept block. Drop it when a nearby block (same beat) delivers the clause complete.
     - **Complete-clause restart-as-bridge** (found 2026-08-26, 25.9min single-source cut — 6 drops + 4 tails + 1 head + 1 span needed, confirming this is a recurring shape, not a one-off): distinct from the cutoff-fragment case above — the block is grammatically whole, no trailing `...` or `--`. After a pause the speaker re-states the tail clause of the block that just ended before continuing with new content (e.g. block ends "...thậm chí đó là người mà ngày xưa đi học còn không bằng bạn." then the next block opens "Thậm chí đó là người mà ngày xưa đi học còn không bằng bạn, thì người đó vừa đăng ảnh..."). Rule-based dedupe can **never** catch this — it only ever compares the START prefixes (`t1[:80]` vs `t2[:80]`) of two adjacent blocks, so a tail-of-A/head-of-B duplicate is architecturally invisible to it regardless of threshold tuning. Fix with a `tail` cut on the earlier block (drop its repeated closing clause) or a `head` cut on the later block (drop its repeated lead-in) — pick whichever keeps the more natural join when read back.
     - **Slate/mic-test surviving as the first kept block** — `auto_slate_end()` returns `hybrid[0]["start"]`, the start of the very first hybrid word. If the recording opens with a literal slate ("Alo một hai ba testing.") that word IS `hybrid[0]`, so nothing gets dropped and the slate itself becomes its own "real" kept block. Always eyeball the first 1–2 kept blocks for slate/mic-test phrasing before building `cuts.json` — a keyword heuristic in the script would misfire on legitimate content, so this stays a manual judgment call.
     - **Full-take duplicates separated by multiple abandoned retries**, where reordered clauses push the SequenceMatcher ratio below the 0.65 threshold even though the two takes say the same thing (e.g. "...cùng Thượng Đế lúc bốn giờ sáng thì hãy để lại..." vs "...lúc bốn giờ sáng, nếu như bạn nào thực hành việc đối thoại với Thượng Đế này thì hãy để lại..."). Two clean, complete deliveries of the same line 20–40s apart — especially with abandoned fragments trailing off in between — means the whole beat got re-recorded, not just a word. Keep the LAST complete delivery, drop the earlier one(s). This is the highest-value catch and the easiest to miss because each individual pair-comparison (ratio, adjacency) looks fine in isolation — only reading the full beat in sequence exposes the duplicate.
     - Intra-block cutoff+restart (gap < 1s, so it never got split into its own block) reads as one run-on string with the abandoned half still glued in — needs a `heads`/`tails`/`spans` cut, not `drops`.
     - **When a reference script/outline is given, use it as the dedupe oracle**: a line that the script only states once (a CTA, a hanging question, a closing line) but that you find delivered in full twice in the transcript is almost always a retake — intentional script repetition is a rhetorical device (anaphora, parallel structure) that itself repeats on the page, not a single line said twice.
3. **Write a decisions JSON** (`cuts.json`): `{"drops":[...], "tails":{"i":"anchor"}, "heads":{...}, "spans":{...}}`. Anchor by a word phrase (normalized match, first occurrence) — make head/span anchors specific (include a distinguishing word) when the phrase repeats inside the block.
4. **Apply**: `python helpers/apply_retake_cuts.py <edit_dir> --cuts cuts.json` → `edl_v2.json`. It **exits 1** on `FAIL` (anchor not found) or `AMBIGUOUS` (a tail/head anchor matching >1x in its block — the cut fires on the FIRST match and can silently eat real content). Only **"all anchors matched OK and unique"** + exit 0 means go.
5. **Verify at the text layer FIRST** — dump `edl_v2.json` transitions and read them; broken/cut-off sentences are obvious in text and cost 5s to catch vs a 30-min render. Fix anchors, re-apply.
6. **Render**: `python helpers/render.py <edit_dir>/edl_v2.json -o <edit_dir>/final.mp4` (loudnorm −14 LUFS default), then self-eval the pixels. **Except Jennie's CC_ talking-head clips → use `helpers/render_raw.py` instead (original audio, no loudnorm, no AAC seam gaps — see "Jennie YouTube standard run" step 6).** `apply_retake_cuts.py` also prints a `repeat-scan` (≥6-word runs still occurring 2×+): read it as a to-check list before rendering.

When Hard Rule 11 (strategy confirmation) can be skipped: only when the user explicitly says "đừng hỏi nữa, cứ thế mà làm" or similar standing directive, AND the work matches one of these worked patterns. New aesthetics, new structures, multi-source assemblies, anything with subtitles/grade/animation — confirmation still required.

Real numbers from the first run (49.65 min Vietnamese monologue, 18.7 GB source): output 30.35 min, cut 38.9%, 118 keep blocks, render time ~30 min.
