#!/usr/bin/env python3
"""Validate project data consistency."""

import json
import os
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(
    os.environ.get("DOUYIN_RESEARCH_ROOT", Path(__file__).resolve().parents[1])
).resolve()
VIDEOS_PATH = PROJECT_ROOT / "data" / "videos.jsonl"
REQUIRED = ["schema_version", "status", "video_id", "url", "note_path", "screenshot_dir", "collected_at"]


def main() -> None:
    errors = []
    rows = []
    if VIDEOS_PATH.exists():
        for line_no, line in enumerate(VIDEOS_PATH.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_no}: invalid json: {exc}")
                continue
            rows.append((line_no, row))

    ids = [row.get("video_id") for _, row in rows]
    for video_id, count in Counter(ids).items():
        if video_id and count > 1:
            errors.append(f"duplicate video_id: {video_id} count={count}")

    for line_no, row in rows:
        for key in REQUIRED:
            if row.get(key) in (None, ""):
                errors.append(f"line {line_no}: missing {key}")
        if "play_addr" in row:
            errors.append(f"line {line_no}: play_addr should not be stored")
        note_path = row.get("note_path")
        if note_path and not (PROJECT_ROOT / note_path).exists():
            errors.append(f"line {line_no}: note missing: {note_path}")
        screenshot_dir = row.get("screenshot_dir")
        if screenshot_dir and not (PROJECT_ROOT / screenshot_dir).exists():
            errors.append(f"line {line_no}: screenshot_dir missing: {screenshot_dir}")

    print(f"records={len(rows)}")
    print(f"errors={len(errors)}")
    for error in errors:
        print(error)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
