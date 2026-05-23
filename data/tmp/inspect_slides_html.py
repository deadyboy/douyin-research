import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.lib.douyin_ssr import _follow_redirect, _http_get, RE_VIDEO_ID

short_url = "https://v.douyin.com/ljGlZCkNm0E/"
final_url = _follow_redirect(short_url)
print("final", final_url[:1000])
video_id = RE_VIDEO_ID.search(final_url).group(1)
for path in [f"https://www.iesdouyin.com/share/slides/{video_id}/", final_url, f"https://www.iesdouyin.com/share/video/{video_id}/"]:
    print("\nURL", path[:500])
    try:
        _, html = _http_get(path, timeout=15)
        print("len", len(html))
        for pat in ["_ROUTER_DATA", "RENDER_DATA", "__INIT_PROPS__", "SIGI_STATE", "item_list", "image_infos", "images"]:
            print(pat, html.find(pat))
        print("scripts", len(re.findall(r"<script", html)))
        print(html[:500].replace("\n", " ")[:500])
    except Exception as e:
        print("ERR", repr(e))
