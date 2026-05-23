# Douyin Agent Video Research

Minimal Hermes project for analyzing user-provided Douyin videos without login or comment scraping.

## Current Flow

```text
Douyin share link
-> SSR metadata and play_addr
-> temporary video read under data/tmp/
-> ffmpeg keyframes and OCR frames
-> local Qwen3-VL vision/OCR
-> notes/*.md and data/videos.jsonl
```

The project-level `.env` points the visual layer to the local OpenAI-compatible
Qwen3-VL service:

```bash
VISION_API_BASE=http://172.18.0.1:18080/v1
VISION_MODEL=qwen3-vl-32b
VISION_API_KEY=EMPTY
```

The main entrypoint is:

```bash
python3 scripts/analyze_video.py "https://v.douyin.com/SHORT_CODE/" --tags agent demo
```

## Daily Use

Analyze one video:

```bash
cd ~/douyin-agent-research
python3 scripts/analyze_video.py "你的抖音短链或分享文本" --tags tag1 tag2
```

Queue a link:

```bash
python3 scripts/add_link.py "你的抖音分享文本" tag1 tag2
```

Process queued links:

```bash
python3 scripts/process_inbox.py --limit 3
```

Generate a report:

```bash
python3 scripts/make_daily_report.py
python3 scripts/make_daily_report.py --date 2026-05-21
```

Maintenance:

```bash
python3 scripts/compact_videos.py --execute
python3 scripts/cleanup_tmp.py --days 2 --execute
```

Check the local Qwen3-VL service from inside the Hermes container:

```bash
python3 - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://172.18.0.1:18080/v1/models", timeout=5).read().decode()[:500])
PY
```

## Structure

```text
douyin-agent-research/
├── AGENTS.md
├── README.md
├── data/
│   ├── inbox.jsonl
│   ├── videos.jsonl
│   ├── failed.jsonl
│   └── tmp/
├── notes/
├── reports/
├── screenshots/
└── scripts/
    ├── analyze_video.py
    ├── add_link.py
    ├── process_inbox.py
    ├── make_daily_report.py
    ├── compact_videos.py
    ├── cleanup_tmp.py
    └── lib/
```

## Constraints

- Analyze only user-provided or queued links.
- Do not log in, bypass restrictions, use Browserbase/CDP, or scrape comments.
- Do not save full video files as artifacts.
- Keep temporary files under `data/tmp/`.
- Use project scripts for cleanup; do not use shell `rm -rf`.

## Output

Each successful analysis produces:

- One Markdown note under `notes/`.
- Keyframes under `screenshots/{video_id}/keyframes/`.
- One upserted structured record in `data/videos.jsonl`.

`data/videos.jsonl` should contain one best current record per `video_id`.
