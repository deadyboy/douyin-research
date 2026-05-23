import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.lib.douyin_ssr import parse_short_url
import urllib.request

url = "https://v.douyin.com/vTsxwbKlHKA/"
meta = parse_short_url(url)
print("video_id", meta.video_id)
print("duration_ms", meta.duration_ms)
print("play_addr", meta.play_addr[:300] if meta.play_addr else None)
print("wm", meta.play_addr_watermark[:300] if meta.play_addr_watermark else None)

headers = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://www.douyin.com/",
}

for name, u in [("play_addr", meta.play_addr), ("wm", meta.play_addr_watermark)]:
    if not u:
        continue
    req = urllib.request.Request(u, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        print(name, "status", resp.status, "url", resp.geturl())
        print(name, "ctype", resp.headers.get("content-type"), "len", resp.headers.get("content-length"))
        print(name, "first", len(resp.read(128)))
    except Exception as e:
        print(name, "ERR", repr(e))
