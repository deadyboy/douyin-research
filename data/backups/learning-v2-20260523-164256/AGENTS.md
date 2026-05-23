# AGENTS.md - Douyin Agent Video Research

## Purpose

This project analyzes Douyin videos explicitly provided by the user for Agent-learning notes.

The current production path is v2:

```text
share link -> SSR metadata + play_addr -> temporary video read -> ffmpeg frames
-> local Qwen3-VL vision/OCR -> Markdown note + videos.jsonl
```

## Hard Rules

- Only analyze links explicitly provided by the user or already present in `data/inbox.jsonl`.
- Do not log in to Douyin, use Browserbase, use CDP, bypass CAPTCHA, bypass privacy restrictions, or scrape comments.
- Do not save full video files as artifacts. Temporary video files must stay under `data/tmp/` and be deleted by the script.
- Do not use shell `rm -rf`. Use project scripts for cleanup.
- Do not write project scripts, JSON, XML, reports, screenshots, or temp files outside this project.

## Project Root

```text
Container: /opt/data/home/douyin-agent-research
Host:      /data2/hermes/data/home/douyin-agent-research
```

Always run from the project root:

```bash
cd ~/douyin-agent-research
```

Allowed write locations:

- `data/`
- `data/tmp/`
- `notes/`
- `screenshots/`
- `reports/`
- `scripts/`

## Main Commands

Analyze one video:

```bash
python3 scripts/analyze_video.py "https://v.douyin.com/SHORT_CODE/" --tags tag1 tag2
```

Add a link to the inbox:

```bash
python3 scripts/add_link.py "raw Douyin share text" tag1 tag2
```

Process pending inbox items:

```bash
python3 scripts/process_inbox.py --limit 3
```

Generate daily report:

```bash
python3 scripts/make_daily_report.py
python3 scripts/make_daily_report.py --date YYYY-MM-DD
```

Clean temporary files safely:

```bash
python3 scripts/cleanup_tmp.py --days 2 --execute
```

Compact duplicate video records:

```bash
python3 scripts/compact_videos.py --execute
```

## Data Contract

`data/videos.jsonl` is the current structured result store. Keep one best record per `video_id`.

Important fields:

- `video_id`, `aweme_id`
- `url`, `short_url`
- `title`, `author`, `description`
- `statistics`, `author_stats`
- `visual_summary`
- `ocr_text`
- `learning_points`
- `keyframe_count`
- `ocr_frame_count`
- `note_path`
- `screenshot_dir`
- `tags`
- `collected_at`

`data/inbox.jsonl` stores queued links. Each record should have:

- `raw_input`
- `url`
- `tags`
- `status`: `pending`, `done`, or `failed`
- `added_at`
- optional `processed_at`, `note_path`, `error`

`data/failed.jsonl` stores failed analysis attempts.

## v2 Analysis Details

The v2 pipeline is implemented by `scripts/analyze_video.py` and helpers under `scripts/lib/`.

It must:

1. Resolve the short link and parse `_ROUTER_DATA`.
2. Extract `video_id`, metadata, statistics, cover, duration, and `play_addr`.
3. Download the accessible public video stream to `data/tmp/downloads/`.
4. Use ffmpeg on the local temporary file.
5. Extract keyframes for scene understanding.
6. Extract OCR frames for visible text/subtitles.
7. Use the project-configured OpenAI-compatible vision endpoint for video, image, and OCR analysis.
8. Use the collected evidence to write a Markdown note.
9. Upsert the structured record in `data/videos.jsonl`.
10. Delete temporary video and OCR files.

Frame strategy:

| Duration | Extracted keyframes | Analyzed keyframes | OCR fps | Analyzed OCR frames |
|---|---:|---:|---:|---:|
| <=15s | 8-12 | <=8 | 1.0 | <=20 |
| 15-60s | 12-20 | <=8 | 1.0 | <=20 |
| 60-180s | 20-36 | <=8 | 0.5 | <=20 |
| >180s | first 180s only | <=8 | 0.5 | <=20 |

## Note Requirements

Each note must distinguish evidence sources:

- Basic metadata from SSR.
- Visual observations from keyframes.
- Visible text/subtitle OCR from frames.
- Learning points synthesized from observable evidence.
- Limitations, especially no comments, no login, no audio ASR.

## Evaluation

The curated smoke-test set is `eval/examples.jsonl`. Use it to avoid optimizing
prompts around a single video.

Quality check:

```bash
python3 scripts/evaluate_quality.py
```

The report must stay under `reports/eval/`. This evaluation is read-only for
Douyin and existing media artifacts.

## Operational Boundaries

- Comments are not part of v2. SSR `comment_list` is normally null.
- Audio ASR is not part of v2.
- Browser automation is not the default path.
- Watermarked `playwm` URLs are acceptable when SSR does not expose a cleaner stream.
- If video download or frame extraction fails, write the failure to `data/failed.jsonl`.
