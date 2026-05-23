#!/usr/bin/env python3
"""
media_extract.py — 下载视频片段并抽取关键帧和 OCR 帧。

采用两步策略避免 ffmpeg 远程读取超时：
1. Python urllib 下载视频到临时文件
2. ffmpeg 从本地文件抽帧
3. 删除临时视频文件

抽帧策略（根据视频时长）：
    <=15s:   视觉关键帧 8-12 张, OCR 1fps
    15-60s:  视觉关键帧 12-20 张, OCR 1fps
    60-180s: 视觉关键帧 20-36 张, OCR 0.5-1fps
    >180s:   只分析前 180 秒（3 分钟）
"""

import glob
import json
import os
import subprocess
import tempfile
import urllib.request
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── 常量 ──────────────────────────────────────────────

MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
REFERER = "https://www.douyin.com/"
MAX_DOWNLOAD_MB = 80  # 短视频通常很小，3 分钟 720p 约 15-30MB
DOWNLOAD_TIMEOUT = 60  # 下载超时秒数
PROJECT_ROOT = Path(os.environ.get("DOUYIN_RESEARCH_ROOT", Path(__file__).resolve().parents[2])).resolve()
TMP_DIR = PROJECT_ROOT / "data" / "tmp"


# ── 抽帧策略参数 ──────────────────────────────────────

@dataclass
class FrameStrategy:
    keyframe_min: int
    keyframe_max: int
    ocr_fps: float
    max_duration: float

    @staticmethod
    def for_duration(sec: float) -> "FrameStrategy":
        if sec <= 15:
            return FrameStrategy(8, 12, 1.0, sec)
        elif sec <= 60:
            return FrameStrategy(12, 20, 1.0, sec)
        elif sec <= 180:
            return FrameStrategy(20, 36, 0.5, sec)
        else:
            return FrameStrategy(20, 36, 0.5, 180.0)


@dataclass
class FrameExtractionResult:
    video_id: str = ""
    duration_sec: float = 0
    duration_truncated: bool = False
    keyframe_dir: str = ""
    ocr_dir: str = ""
    keyframe_count: int = 0
    ocr_count: int = 0
    keyframe_paths: list = field(default_factory=list)
    ocr_paths: list = field(default_factory=list)


# ── 下载视频 ──────────────────────────────────────────

def _download_video(
    url: str,
    max_bytes: int = MAX_DOWNLOAD_MB * 1024 * 1024,
    video_id: str = "",
) -> Optional[str]:
    """用 Python urllib 下载视频到临时文件，返回路径。"""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", MOBILE_UA)
    req.add_header("Referer", REFERER)
    
    download_dir = TMP_DIR / (video_id or "downloads")
    download_dir.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        suffix=".mp4",
        prefix="video-",
        dir=str(download_dir),
        delete=False,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT)
        downloaded = 0
        while downloaded < max_bytes:
            chunk = resp.read(65536)
            if not chunk:
                break
            tmp.write(chunk)
            downloaded += len(chunk)
        tmp.close()
        if downloaded == 0:
            os.unlink(tmp.name)
            return None
        return tmp.name
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None


def _get_duration(local_path: str) -> float:
    """用 ffprobe 获取本地视频时长。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", local_path],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0


# ── 抽帧 ──────────────────────────────────────────────

def _extract_ocr_frames(local_path: str, ocr_dir: str, fps: float,
                        max_duration: float) -> list[str]:
    """从本地视频抽取 OCR 帧。"""
    os.makedirs(ocr_dir, exist_ok=True)
    out_pattern = os.path.join(ocr_dir, "ocr_%04d.png")
    
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", "0",
        "-i", local_path,
        "-t", str(max_duration),
        "-vf", f"fps={fps}",
        out_pattern
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return sorted(glob.glob(os.path.join(ocr_dir, "ocr_*.png")))


def _extract_keyframes(local_path: str, keyframe_dir: str,
                       strategy: FrameStrategy, effective_duration: float) -> list[str]:
    """
    从本地视频抽取关键帧。
    先用场景检测，不足则均匀采样补充。
    """
    os.makedirs(keyframe_dir, exist_ok=True)
    
    # Phase 1: 场景检测
    scene_pattern = os.path.join(keyframe_dir, "scene_%04d.png")
    scene_cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", "0",
        "-i", local_path,
        "-t", str(effective_duration),
        "-vf", "select='gt(scene,0.3)',setpts=N/FRAME_RATE/TB",
        "-vsync", "vfr",
        "-frames:v", str(strategy.keyframe_max),
        scene_pattern
    ]
    subprocess.run(scene_cmd, capture_output=True, text=True, timeout=120)
    scene_files = sorted(glob.glob(os.path.join(keyframe_dir, "scene_*.png")))
    
    # Phase 2: 不足则均匀采样补充
    if len(scene_files) < strategy.keyframe_min:
        needed = strategy.keyframe_min - len(scene_files)
        interval = effective_duration / max(strategy.keyframe_min, 1)
        uniform_pattern = os.path.join(keyframe_dir, "uniform_%04d.png")
        uniform_cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", "0",
            "-i", local_path,
            "-t", str(effective_duration),
            "-vf", f"fps=1/{interval:.2f}",
            "-frames:v", str(needed),
            uniform_pattern
        ]
        subprocess.run(uniform_cmd, capture_output=True, text=True, timeout=120)
    
    all_frames = sorted(glob.glob(os.path.join(keyframe_dir, "*.png")))
    
    # Phase 3: 超出上限则均匀采样裁剪
    if len(all_frames) > strategy.keyframe_max:
        step = len(all_frames) / strategy.keyframe_max
        keep = {all_frames[int(i * step)] for i in range(strategy.keyframe_max)}
        for f in all_frames:
            if f not in keep:
                try:
                    os.remove(f)
                except OSError:
                    pass
        return sorted(keep)
    
    return sorted(all_frames)


# ── 主函数 ──────────────────────────────────────────

def extract_frames(
    play_addr: str,
    output_root: str,
    duration_sec: Optional[float] = None,
    video_id: str = ""
) -> FrameExtractionResult:
    """
    下载视频并抽取关键帧 + OCR 帧。
    """
    result = FrameExtractionResult(video_id=video_id)
    
    keyframe_dir = os.path.join(output_root, "keyframes")
    ocr_dir = str(TMP_DIR / (video_id or "unknown") / "ocr")
    result.keyframe_dir = keyframe_dir
    result.ocr_dir = ocr_dir
    
    # Step 1: 下载视频
    local_path = _download_video(play_addr, video_id=video_id)
    if not local_path:
        # 记录失败但继续（可能只是网络问题）
        return result
    
    try:
        # Step 2: 获取时长
        detected_dur = _get_duration(local_path)
        actual_duration = duration_sec if (duration_sec and duration_sec > 0) else detected_dur
        if actual_duration <= 0:
            actual_duration = 30  # 兜底
        result.duration_sec = actual_duration
        
        # Step 3: 确定策略
        strategy = FrameStrategy.for_duration(actual_duration)
        effective_duration = min(actual_duration, strategy.max_duration)
        result.duration_truncated = (actual_duration > strategy.max_duration)
        
        # Step 4: 抽取 OCR 帧
        result.ocr_paths = _extract_ocr_frames(local_path, ocr_dir, strategy.ocr_fps, effective_duration)
        result.ocr_count = len(result.ocr_paths)
        
        # Step 5: 抽取关键帧
        result.keyframe_paths = _extract_keyframes(local_path, keyframe_dir, strategy, effective_duration)
        result.keyframe_count = len(result.keyframe_paths)
    
    finally:
        # Step 6: 删除临时视频
        try:
            os.unlink(local_path)
        except OSError:
            pass
    
    return result


def cleanup_ocr_frames(ocr_dir: str) -> None:
    """清理 OCR 临时帧目录。"""
    target = Path(ocr_dir).resolve()
    allowed_roots = [
        (PROJECT_ROOT / "screenshots").resolve(),
        TMP_DIR.resolve(),
    ]
    if not target.is_dir() or target in allowed_roots:
        return
    if not any(target.is_relative_to(root) for root in allowed_roots):
        return
    shutil.rmtree(target)


# ── 命令行测试 ──────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python media_extract.py <play_addr> [duration_sec] [output_dir]")
        sys.exit(1)
    
    url = sys.argv[1]
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else None
    out = sys.argv[3] if len(sys.argv) > 3 else str(TMP_DIR / "frame_test")
    
    result = extract_frames(url, out, dur)
    print(f"Duration: {result.duration_sec:.1f}s")
    print(f"Truncated: {result.duration_truncated}")
    print(f"Keyframes: {result.keyframe_count}")
    print(f"OCR frames: {result.ocr_count}")
    for kf in result.keyframe_paths[:3]:
        print(f"  KF: {kf}")
    for oc in result.ocr_paths[:3]:
        print(f"  OCR: {oc}")
