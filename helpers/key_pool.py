"""SSOT kho xoay key ElevenLabs: đánh dấu key hết credit để pick_elevenlabs_key.py bỏ qua.

Mọi chỗ gọi Scribe/ElevenLabs gặp `quota_exceeded` phải gọi mark_exhausted(key) trước khi thoát
(sự cố 24/11/2026: picker cứ chọn lại key 188 credit vì không ai đánh dấu). Không bao giờ ném lỗi.
"""
import json
import sys
import time
from pathlib import Path

POOL_STORE = Path("C:/Users/Jennie Chu/Desktop/Projects/crypto-pattern-scanner/memory/personal/elevenlabs-keys.json")


def mark_exhausted(key: str, store: Path = POOL_STORE) -> None:
    try:
        d = json.loads(store.read_text(encoding="utf-8"))
        for k in d.get("keys", []):
            if k.get("key") == key:
                k["exhausted_month"] = time.strftime("%Y-%m")
                store.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  ⚠ key {k.get('id')} quota_exceeded → marked exhausted_month; rerun to rotate", file=sys.stderr)
                return
    except Exception as e:
        print(f"  (could not mark key exhausted: {e})", file=sys.stderr)
