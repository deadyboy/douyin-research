#!/usr/bin/env python3
"""
analyze_video.py — 抖音视频内容分析 v2 主流程。

流程：
1. SSR 解析短链 → 获取元数据 + play_addr
2. 下载临时视频到 data/tmp/
3. OpenAI-compatible 视觉模型直接分析 video_url，生成主时间轴
4. ffmpeg scene-change 抽帧复核关键细节
5. OCR 帧作为可见文字兜底
5. 生成 Markdown 分析笔记
6. Upsert 结构化数据到 videos.jsonl

用法：
    python3 scripts/analyze_video.py "https://v.douyin.com/IwKUHooX8us/" [--tags 标签1 标签2]
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 项目路径 ──────────────────────────────────────────

_PROJECT_ROOT = Path(
    os.environ.get("DOUYIN_RESEARCH_ROOT", Path(__file__).resolve().parents[1])
).resolve()

_DATA_DIR = _PROJECT_ROOT / "data"
_NOTES_DIR = _PROJECT_ROOT / "notes"
_SCREENSHOTS_DIR = _PROJECT_ROOT / "screenshots"
_TMP_DIR = _DATA_DIR / "tmp"

CST = timezone(timedelta(hours=8))

# 确保目录存在
for d in [_DATA_DIR, _NOTES_DIR, _SCREENSHOTS_DIR, _TMP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 添加 scripts/lib 到路径
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from lib.douyin_ssr import parse_short_url, VideoMeta
from lib.media_extract import (
    extract_frames,
    extract_frames_from_local,
    download_video_artifact,
    cleanup_ocr_frames,
)
from lib.vision_ocr import (
    analyze_keyframes, compose_scene_summary,
    analyze_ocr_frames, compose_subtitle_text,
    analyze_video_file, analyze_frame_verification, analyze_image_post,
    ENABLED as VISION_ENABLED,
    VISION_MODEL,
    vision_backend_label,
)


# ── 辅助函数 ──────────────────────────────────────────

def _timestamp() -> str:
    return datetime.now(CST).isoformat()


def _date_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _download_image_evidence(urls: list[str], video_id: str, max_images: int = 20) -> list[str]:
    """Download image-post media as evidence frames under screenshots/{video_id}/images."""
    if not urls:
        return []
    image_dir = _SCREENSHOTS_DIR / video_id / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": "https://www.douyin.com/",
    }
    for index, url in enumerate(urls[:max_images], 1):
        suffix = ".jpg"
        lower = url.lower()
        if ".webp" in lower:
            suffix = ".webp"
        elif ".png" in lower:
            suffix = ".png"
        path = image_dir / f"image_{index:04d}{suffix}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp, path.open("wb") as f:
                f.write(resp.read(20 * 1024 * 1024))
            if path.stat().st_size > 0:
                paths.append(str(path))
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
    return paths


# ── 主流程 ────────────────────────────────────────────

def analyze_video(short_url: str, tags: list[str] | None = None) -> dict:
    """
    完整分析一条抖音视频。
    
    Returns:
        dict 包含 video_id, note_path, play_addr, keyframe_count, ocr_count, ...
    """
    tags = tags or []
    result = {
        "short_url": short_url,
        "tags": tags,
        "status": "pending",
        "started_at": _timestamp(),
        "errors": [],
    }
    
    # ═══════════════════════════════════════════════════════
    # Phase 1: SSR 元数据提取
    # ═══════════════════════════════════════════════════════
    try:
        meta = parse_short_url(short_url)
    except Exception as e:
        result["status"] = "failed"
        result["error"] = f"SSR 解析失败: {e}"
        _record_failed(short_url, str(e))
        return result
    
    video_id = meta.video_id
    duration_sec = meta.duration_ms / 1000 if meta.duration_ms else 0
    
    result["video_id"] = video_id
    result["meta"] = meta
    
    # ═══════════════════════════════════════════════════════
    # Phase 2: 下载视频 + video-first 分析 + 抽帧
    # ═══════════════════════════════════════════════════════
    frame_output_root = str(_SCREENSHOTS_DIR / video_id)
    frame_result = None
    video_summary = ""
    video_usage = {}
    video_first_ok = False
    local_video_path = None
    if meta.play_addr:
        try:
            local_video_path = download_video_artifact(meta.play_addr, video_id=video_id)
            if local_video_path:
                if VISION_ENABLED:
                    video_analysis = analyze_video_file(
                        local_video_path,
                        title=meta.title,
                        description=meta.description,
                        duration_sec=duration_sec,
                    )
                    video_summary = video_analysis.summary
                    video_usage = video_analysis.usage
                    video_first_ok = video_analysis.ok
                    if not video_analysis.ok:
                        result["errors"].append(f"video-first failed: {video_analysis.error}")

                frame_result = extract_frames_from_local(
                    local_path=local_video_path,
                    output_root=frame_output_root,
                    duration_sec=duration_sec if duration_sec > 0 else None,
                    video_id=video_id,
                )
            else:
                result["errors"].append("video download failed; falling back to frame extractor")
                frame_result = extract_frames(
                    play_addr=meta.play_addr,
                    output_root=frame_output_root,
                    duration_sec=duration_sec if duration_sec > 0 else None,
                    video_id=video_id,
                )
        except Exception as e:
            result["frame_error"] = str(e)
            result["errors"].append(f"frame extraction failed: {e}")
        finally:
            if local_video_path:
                try:
                    os.unlink(local_video_path)
                except OSError:
                    pass
    else:
        result["frame_error"] = "无 play_addr"
        result["errors"].append("missing play_addr")
    
    # ═══════════════════════════════════════════════════════
    # Phase 3: scene-change 复核 + OCR 兜底
    # ═══════════════════════════════════════════════════════
    scene_summary = ""
    frame_verification = ""
    subtitle_text = ""
    keyframe_count = 0
    frame_verified_count = 0
    ocr_count = 0
    
    if frame_result and frame_result.keyframe_paths:
        keyframe_count = frame_result.keyframe_count
        if VISION_ENABLED:
            if video_first_ok:
                frame_verify_max = int(os.environ.get("DOUYIN_FRAME_VERIFY_MAX", "64"))
                frame_verified_count = min(keyframe_count, frame_verify_max)
                frame_verification = analyze_frame_verification(
                    frame_result.keyframe_paths,
                    video_summary=video_summary,
                    actual_duration=(
                        frame_result.duration_sec
                        if not frame_result.duration_truncated
                        else min(frame_result.duration_sec, float(os.environ.get("DOUYIN_MAX_ANALYZE_SECONDS", "600")))
                    ),
                    max_frames=frame_verify_max,
                )
                scene_summary = (
                    "## Video-first 时间轴主分析\n\n"
                    f"{video_summary or '（video-first 未返回内容）'}\n\n"
                    "## Scene-change 关键帧复核\n\n"
                    f"{frame_verification}"
                )
            else:
                max_kf_analyze = int(os.environ.get("DOUYIN_LEGACY_KEYFRAME_ANALYZE_MAX", "24"))
                analyses = analyze_keyframes(
                    frame_result.keyframe_paths,
                    max_frames=max_kf_analyze,
                    actual_duration=(
                        frame_result.duration_sec
                        if not frame_result.duration_truncated
                        else min(frame_result.duration_sec, float(os.environ.get("DOUYIN_MAX_ANALYZE_SECONDS", "600")))
                    )
                )
                frame_verified_count = min(keyframe_count, max_kf_analyze)
                scene_summary = compose_scene_summary(analyses)
        else:
            scene_summary = "（视觉模型 API 不可用，未进行画面分析）"
    else:
        scene_summary = video_summary or "（未提取到关键帧，可能 play_addr 不可用或视频无法访问）"

    image_evidence_paths = []
    if keyframe_count == 0 and meta.image_urls and VISION_ENABLED:
        image_evidence_paths = _download_image_evidence(meta.image_urls, video_id)
        if image_evidence_paths:
            keyframe_count = len(image_evidence_paths)
            frame_verified_count = len(image_evidence_paths)
            image_summary = analyze_image_post(
                image_evidence_paths,
                title=meta.title,
                description=meta.description,
            )
            scene_summary = (
                "## Image-post 画面分析\n\n"
                "该条目在 SSR 中包含图片媒体，视频播放地址疑似为背景音乐或不可抽帧媒体；"
                "以下基于图片证据分析。\n\n"
                f"{image_summary}"
            )
            result["errors"] = [e for e in result["errors"] if "video-first failed" not in e]
    
    if frame_result and frame_result.ocr_paths:
        ocr_count = frame_result.ocr_count
        if VISION_ENABLED:
            max_ocr_frames = int(os.environ.get("DOUYIN_OCR_MAX_FRAMES", "80"))
            ocr_results = analyze_ocr_frames(frame_result.ocr_paths, max_frames=max_ocr_frames)
            subtitle_text = compose_subtitle_text(ocr_results)
        else:
            subtitle_text = "（视觉模型 API 不可用，未进行 OCR）"
    else:
        subtitle_text = "（未提取到 OCR 帧）"
    
    # ═══════════════════════════════════════════════════════
    # Phase 4: 清理 OCR 临时帧
    # ═══════════════════════════════════════════════════════
    if frame_result:
        cleanup_ocr_frames(frame_result.ocr_dir)
    
    # ═══════════════════════════════════════════════════════
    # Phase 4.5: 合成学习要点
    # ═══════════════════════════════════════════════════════
    learning_points = _synthesize_learning_points(
        title=meta.title,
        description=meta.description,
        scene_summary=scene_summary,
        subtitle_text=subtitle_text,
    )
    
    # ═══════════════════════════════════════════════════════
    # Phase 5: 生成 Markdown 笔记
    # ═══════════════════════════════════════════════════════
    note_path = _NOTES_DIR / f"{_date_str()}-{video_id}.md"
    _write_note(
        meta=meta,
        scene_summary=scene_summary,
        subtitle_text=subtitle_text,
        learning_points=learning_points,
        keyframe_count=keyframe_count,
        ocr_count=ocr_count,
        duration_truncated=frame_result.duration_truncated if frame_result else False,
        effective_duration=frame_result.duration_sec if frame_result else 0,
        video_first_ok=video_first_ok,
        frame_verified_count=frame_verified_count,
        video_usage=video_usage,
        note_path=note_path,
        tags=tags,
    )
    
    # ═══════════════════════════════════════════════════════
    # Phase 6: upsert videos.jsonl
    # ═══════════════════════════════════════════════════════
    _append_videos_jsonl(
        meta=meta,
        scene_summary=scene_summary,
        subtitle_text=subtitle_text,
        learning_points=learning_points,
        note_path=str(note_path.relative_to(_PROJECT_ROOT)),
        screenshot_dir=str(Path(frame_output_root).relative_to(_PROJECT_ROOT)),
        tags=tags,
        keyframe_count=keyframe_count,
        frame_verified_count=frame_verified_count,
        ocr_count=ocr_count,
        video_summary=video_summary,
        frame_verification=frame_verification,
        video_usage=video_usage,
        video_first_ok=video_first_ok,
        image_evidence_count=len(image_evidence_paths),
        status="completed" if (video_first_ok or keyframe_count > 0) else "partial",
        errors=result["errors"],
    )
    
    # 收尾
    result["status"] = "completed" if (video_first_ok or keyframe_count > 0) else "partial"
    result["note_path"] = str(note_path.relative_to(_PROJECT_ROOT))
    result["keyframe_count"] = keyframe_count
    result["frame_verified_count"] = frame_verified_count
    result["ocr_count"] = ocr_count
    result["video_first_ok"] = video_first_ok
    result["image_evidence_count"] = len(image_evidence_paths)
    result["play_addr_present"] = bool(meta.play_addr)
    result["completed_at"] = _timestamp()
    
    return result


# ── 学习要点合成 ──────────────────────────────────────────

def _synthesize_learning_points(
    title: str,
    description: str,
    scene_summary: str,
    subtitle_text: str,
) -> str:
    """Synthesize learning points from observable evidence.

    Prefer the project-local OpenAI-compatible endpoint so this works inside
    the Docker deployment without relying on a separate school API key.
    """
    import urllib.request as _ur
    import urllib.error as _ue
    
    prompt = f"""你是一个视频内容分析助手。请根据以下证据，提取 3-6 条学习要点。每条要独立、有信息量、可直接引用。用中文输出。

格式要求：
- 每条以「- 」开头
- 每条 1-3 句
- 不要重复标题和基本信息
- 不要输出「没有足够信息」——尽你所能从证据中提取

=== 标题 ===
{title}

=== 文案 ===
{description}

=== 画面分析 ===
{scene_summary[:3000]}

=== OCR 字幕文字 ===
{subtitle_text[:3000]}

学习要点："""

    payload = {
        "model": os.environ.get("LEARNING_MODEL") or os.environ.get("VISION_MODEL", VISION_MODEL),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    candidates = [
        (
            os.environ.get("LEARNING_API_BASE", "").rstrip("/"),
            os.environ.get("LEARNING_MODEL") or os.environ.get("VISION_MODEL", VISION_MODEL),
            os.environ.get("LEARNING_API_KEY", ""),
        ),
        (
            os.environ.get("VISION_API_BASE", "").rstrip("/"),
            os.environ.get("VISION_MODEL", VISION_MODEL),
            os.environ.get("VISION_API_KEY", ""),
        ),
        (
            os.environ.get("API_BASE", "").rstrip("/"),
            os.environ.get("MODEL", "qwen-chat"),
            os.environ.get("USTC_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
        ),
    ]
    errors = []
    try:
        for api_base, model, api_key in candidates:
            if not api_base or not model:
                continue
            payload["model"] = model
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = _ur.Request(
                f"{api_base}/chat/completions",
                data=json.dumps(payload).encode(),
                headers=headers,
            )
            try:
                resp = _ur.urlopen(req, timeout=90)
                data = json.loads(resp.read())
                content = data["choices"][0]["message"]["content"].strip()
                if content:
                    return content
                errors.append(f"{model}@{api_base}: empty response")
            except (_ue.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as e:
                errors.append(f"{model}@{api_base}: {type(e).__name__}")

        return "（学习要点合成失败：" + "; ".join(errors[:3]) + "）"
    except Exception as e:
        return f"（学习要点合成失败：{type(e).__name__}）"


# ── 笔记生成 ──────────────────────────────────────────

def _write_note(
    meta: VideoMeta,
    scene_summary: str,
    subtitle_text: str,
    learning_points: str,
    keyframe_count: int,
    ocr_count: int,
    duration_truncated: bool,
    effective_duration: float,
    video_first_ok: bool,
    frame_verified_count: int,
    video_usage: dict,
    note_path: Path,
    tags: list[str],
) -> None:
    """生成 Markdown 分析笔记。"""
    duration_display = f"约 {meta.duration_ms/1000:.0f} 秒（{meta.duration_ms}ms）"
    duration_hint = ""
    if duration_truncated:
        duration_hint = f"\n> ⚠️ 视频时长超过 3 分钟，仅分析了前 {effective_duration/60:.0f} 分钟。"
    
    hashtag_display = ", ".join(f"`{t}`" for t in meta.hashtags) if meta.hashtags else "无"
    tag_display = ", ".join(f"`{t}`" for t in tags) if tags else "无"
    vision_label = vision_backend_label() if VISION_ENABLED else "视觉模型未启用"
    video_first_display = "成功" if video_first_ok else "未成功/未启用"
    usage_display = ""
    if video_usage:
        usage_display = (
            f"prompt={video_usage.get('prompt_tokens', 'N/A')}, "
            f"completion={video_usage.get('completion_tokens', 'N/A')}, "
            f"total={video_usage.get('total_tokens', 'N/A')}"
        )
    
    content = f"""# {meta.title or '(无标题)'}

**来源**：{meta.raw_url}
**短链**：{meta.short_url}
**作者**：{meta.author}{' ( @' + meta.author_unique_id + ' )' if meta.author_unique_id else ''}
**video_id**：`{meta.video_id}`
**采集时间**：{_timestamp()}
**时长**：{duration_display}{duration_hint}

## 📋 视频信息

| 属性 | 值 |
|------|-----|
| 文案 | {meta.description or '无'} |
| 话题 | {hashtag_display} |
| 配乐 | {meta.music or '未识别'} |
| 用户标签 | {tag_display} |

## 📊 数据表现

| 指标 | 数值 |
|------|------|
| 👍 点赞 | {meta.statistics.get('digg_count', 0):,} |
| 💬 评论 | {meta.statistics.get('comment_count', 0):,} |
| 🔄 分享 | {meta.statistics.get('share_count', 0):,} |
| ⭐ 收藏 | {meta.statistics.get('collect_count', 0):,} |
| ▶️ 播放 | {meta.statistics.get('play_count', 0):,} |

## 👤 作者信息

| 属性 | 值 |
|------|-----|
| 昵称 | {meta.author} |
| 抖音号 | {meta.author_unique_id} |
| 作品数 | {meta.author_stats.get('aweme_count', 'N/A')} |
| 粉丝数 | {meta.author_stats.get('followers', 'N/A'):,} |

## 🎬 Video-first 画面分析

> **主分析**：直接输入视频文件，状态：{video_first_display}
> **复核证据**：scene-change 关键帧共 {keyframe_count} 张，其中 {frame_verified_count} 张送入模型复核
> **分析模型**：{vision_label}
> **Video token usage**：{usage_display or 'N/A'}

{scene_summary}

## 📝 可见字幕 / 文字（OCR）

> **证据来源**：基于 OCR 识别（{ocr_count} 张 OCR 帧，{vision_label}）
> ⚠️ 不做音频 ASR，以下仅为视频中**可见文字**的提取结果。

{subtitle_text}

## 🔍 学习要点

> ⚠️ 以下为 AI 分析，基于标题、画面、字幕的**可观察证据**，非确定性结论。

{learning_points}

---

*本笔记由 Hermes 抖音 Agent 视频研究助理 v2 自动生成*
*视觉模型: {vision_label} | 主流程: video-first + scene-change frame verification + OCR fallback*
"""
    
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(content)


# ── 结构化数据 upsert ─────────────────────────────────

def _record_score(record: dict) -> tuple[int, int, int, str]:
    """Prefer video-first records, frame evidence, OCR evidence, then latest timestamp."""
    return (
        1 if record.get("video_first_ok") else 0,
        int(record.get("keyframe_count") or 0),
        int(record.get("ocr_frame_count") or 0),
        str(record.get("collected_at") or ""),
    )

def _append_videos_jsonl(
    meta: VideoMeta,
    scene_summary: str,
    subtitle_text: str,
    learning_points: str,
    note_path: str,
    screenshot_dir: str,
    tags: list[str],
    keyframe_count: int,
    frame_verified_count: int,
    ocr_count: int,
    video_summary: str,
    frame_verification: str,
    video_usage: dict,
    video_first_ok: bool,
    image_evidence_count: int,
    status: str,
    errors: list[str],
) -> None:
    """Upsert into data/videos.jsonl, keeping one best record per video_id."""
    record = {
        "schema_version": 2,
        "status": status,
        "video_id": meta.video_id,
        "aweme_id": meta.aweme_id,
        "url": meta.raw_url,
        "short_url": meta.short_url,
        "title": meta.title,
        "author": meta.author,
        "author_unique_id": meta.author_unique_id,
        "description": meta.description,
        "hashtags": meta.hashtags,
        "music": meta.music,
        "duration_ms": meta.duration_ms,
        "play_addr_present": bool(meta.play_addr),
        "statistics": meta.statistics,
        "author_stats": meta.author_stats,
        "visual_summary": scene_summary,
        "video_first_summary": video_summary,
        "frame_verification": frame_verification,
        "ocr_text": subtitle_text,
        "learning_points": learning_points,
        "keyframe_count": keyframe_count,
        "frame_verified_count": frame_verified_count,
        "ocr_frame_count": ocr_count,
        "note_path": note_path,
        "screenshot_dir": screenshot_dir,
        "tags": tags,
        "errors": errors,
        "collected_at": _timestamp(),
        "source": "SSR HTML (_ROUTER_DATA) + direct video/image input + ffmpeg scene-change verification",
        "analysis_mode": (
            "image-post+vision-analysis"
            if image_evidence_count and not video_first_ok
            else "video-first+scene-verification+ocr-fallback"
        ),
        "video_first_ok": video_first_ok,
        "image_evidence_count": image_evidence_count,
        "video_usage": video_usage,
        "vision_model": VISION_MODEL if VISION_ENABLED else "",
        "vision_backend": vision_backend_label() if VISION_ENABLED else "",
    }
    
    videos_path = _DATA_DIR / "videos.jsonl"
    other_records = []
    same_video_records = [record]
    if videos_path.exists():
        with videos_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if existing.get("video_id") == meta.video_id:
                    same_video_records.append(existing)
                else:
                    other_records.append(existing)

    best_record = max(same_video_records, key=_record_score)
    records = other_records + [best_record]
    with videos_path.open("w", encoding="utf-8") as f:
        for item in records:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _record_failed(url: str, reason: str) -> None:
    """记录失败。"""
    record = {
        "url": url,
        "reason": reason,
        "attempted_at": _timestamp(),
    }
    failed_path = _DATA_DIR / "failed.jsonl"
    with open(failed_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── 入口 ──────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="抖音视频内容分析 v2")
    parser.add_argument("url", help="抖音短链")
    parser.add_argument("--tags", nargs="*", default=[], help="标签")
    args = parser.parse_args()
    
    print(f"🔍 开始分析: {args.url}")
    result = analyze_video(args.url, args.tags)
    
    if result["status"] in {"completed", "partial"}:
        label = "✅ 分析完成" if result["status"] == "completed" else "⚠️ 部分完成"
        print(label)
        print(f"   video_id: {result['video_id']}")
        print(f"   play_addr_present: {result['play_addr_present']}")
        print(f"   video_first_ok: {result['video_first_ok']}")
        print(f"   scene-change关键帧: {result['keyframe_count']} 张")
        print(f"   复核帧: {result['frame_verified_count']} 张")
        print(f"   图片证据: {result['image_evidence_count']} 张")
        print(f"   OCR 帧: {result['ocr_count']} 张")
        print(f"   笔记: {result['note_path']}")
        if result.get("errors"):
            print(f"   errors: {result['errors']}")
    else:
        print(f"❌ 分析失败: {result.get('error', '未知错误')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
