import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.lib.douyin_ssr import _follow_redirect, _http_get, _extract_json_from_script, RE_VIDEO_ID

short_url = "https://v.douyin.com/vTsxwbKlHKA/"
final_url = _follow_redirect(short_url)
video_id = (RE_VIDEO_ID.search(final_url).group(1) or RE_VIDEO_ID.search(final_url).group(2))
_, html = _http_get(f"https://www.iesdouyin.com/share/video/{video_id}/")
router_data = _extract_json_from_script(html, "window._ROUTER_DATA")
loader = router_data.get("loaderData", {})
page_key = next(k for k in loader if "/page" in k)
item = loader[page_key]["videoInfoRes"]["item_list"][0]
video = item.get("video", {})
print("video_id", video_id)
print("item keys", sorted(item.keys()))
for image_key in ["images", "image_infos", "img_list"]:
    images = item.get(image_key)
    print(image_key, type(images).__name__, len(images) if isinstance(images, list) else "")
    if isinstance(images, list):
        for i, img in enumerate(images[:5], 1):
            print("IMAGE", i, img if not isinstance(img, dict) else {k: img.get(k) for k in img.keys() if k in {"uri", "url_list", "download_url_list", "width", "height"}})
print("video keys", sorted(video.keys()))
for key in sorted(video.keys()):
    val = video[key]
    if isinstance(val, dict):
        urls = val.get("url_list")
        if urls:
            print("\nKEY", key)
            for u in urls[:5]:
                print(u[:1000])
        for sub in ["uri", "url_key", "data_size", "width", "height"]:
            if sub in val:
                print(key, sub, val[sub])
    elif isinstance(val, (str, int, float)):
        print(key, val)
