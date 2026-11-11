"""Chọn key ElevenLabs còn đủ credit từ kho xoay tua.

Kho: <crypto-pattern-scanner>/memory/personal/elevenlabs-keys.json (gitignored).
Dùng:  python pick_elevenlabs_key.py [--need 2000] [--list]
In ra key đầu tiên đủ credit (stdout); --list in bảng credit từng key (không lộ key).
Key không đọc được credit: đánh dấu "exhausted_month":"YYYY-MM" khi gặp quota_exceeded, sang tháng tự mở lại.
Dùng:  export ELEVENLABS_API_KEY=$(python pick_elevenlabs_key.py --need 2000)
"""
import argparse, json, sys, time, urllib.request
from pathlib import Path

STORE = Path("C:/Users/Jennie Chu/Desktop/Projects/crypto-pattern-scanner/memory/personal/elevenlabs-keys.json")


def credits(key):
    req = urllib.request.Request("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key})
    try:
        s = json.load(urllib.request.urlopen(req, timeout=20))
        return s["character_limit"] - s["character_count"], s.get("next_character_count_reset_unix")
    except Exception as e:
        return None, str(e)[:60]


ap = argparse.ArgumentParser()
ap.add_argument("--need", type=int, default=2000)
ap.add_argument("--list", action="store_true")
a = ap.parse_args()
keys = json.loads(STORE.read_text(encoding="utf-8"))["keys"]
pick = None
for k in keys:
    left, reset = credits(k["key"])
    if left is None and k.get("exhausted_month") != time.strftime("%Y-%m"):
        left = 10**9  # key hạn chế quyền (401) không đọc được credit -> thử, hết thì đánh dấu exhausted_month
    elif left is None:
        left = 0
    if a.list:
        r = time.strftime("%d/%m", time.localtime(reset)) if isinstance(reset, int) else reset
        print(f'{k["id"]}: còn {left} credit, reset {r}', file=sys.stderr)
    if pick is None and left is not None and left >= a.need:
        pick = k["key"]
if pick:
    if not a.list:
        print(pick)
else:
    sys.exit("Không key nào đủ credit — thêm key vào elevenlabs-keys.json")
