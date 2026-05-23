import json
from pathlib import Path

root = Path("/data2/hermes/data/home/douyin-agent-research")
videos = root / "data/videos.jsonl"
note = root / "notes/2026-05-22-7641271379199742577.md"

rows = [json.loads(line) for line in videos.read_text(encoding="utf-8").splitlines() if line.strip()]
errors = []
ids = {}
for row in rows:
    ids[row.get("video_id")] = ids.get(row.get("video_id"), 0) + 1
for video_id, count in ids.items():
    if video_id and count > 1:
        errors.append(f"duplicate video_id {video_id}: {count}")
for row in rows:
    for key in ["schema_version", "status", "video_id", "url", "note_path", "screenshot_dir", "collected_at"]:
        if row.get(key) in (None, ""):
            errors.append(f"missing {key} in {row.get('video_id')}")
    if "play_addr" in row:
        errors.append(f"play_addr stored in {row.get('video_id')}")
    if row.get("note_path") and not (root / row["note_path"]).exists():
        errors.append(f"missing note {row['note_path']}")
    if row.get("screenshot_dir") and not (root / row["screenshot_dir"]).exists():
        errors.append(f"missing screenshot dir {row['screenshot_dir']}")

target = [row for row in rows if row.get("video_id") == "7641271379199742577"][-1]
text = note.read_text(encoding="utf-8")
needles = [
    "Video-first 画面分析",
    "Video-first 时间轴主分析",
    "Scene-change 关键帧复核",
    "OCR",
    "video-first + scene-change frame verification",
]
for needle in needles:
    if needle not in text:
        errors.append(f"note missing section text: {needle}")

print(f"records={len(rows)}")
print(f"errors={len(errors)}")
for error in errors:
    print(error)
print("analysis_mode", target.get("analysis_mode"))
print("video_first_ok", target.get("video_first_ok"))
print("counts", target.get("keyframe_count"), target.get("frame_verified_count"), target.get("ocr_frame_count"))
print("vision_model", target.get("vision_model"))
print("has_video_first_summary", bool(target.get("video_first_summary")))
print("has_frame_verification", bool(target.get("frame_verification")))
print("note_size", note.stat().st_size)
if errors:
    raise SystemExit(1)
