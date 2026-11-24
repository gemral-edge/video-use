"""Máy kiểm: Scribe quota_exceeded phải đánh dấu exhausted_month để lần sau xoay key.

Gốc 24/11/2026: EL-01 còn 188 credit, picker coi là không giới hạn (401 không đọc credit)
và bước đánh dấu chưa từng có trong code. Chạy: python helpers/test_quota_mark.py  (exit 0 = OK)
"""
import json, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import aggressive_cut_vi as acv  # noqa: E402

src = Path(acv.__file__).read_text(encoding="utf-8")
bad = 0
if "_mark_pool_key_exhausted(key)" not in src.split("def call_scribe", 1)[1].split("\ndef ", 1)[0]:
    print("FAIL: call_scribe không còn gọi _mark_pool_key_exhausted khi quota_exceeded"); bad += 1
with tempfile.TemporaryDirectory() as d:
    store = Path(d) / "keys.json"
    store.write_text(json.dumps({"keys": [{"id": "A", "key": "k1"}, {"id": "B", "key": "k2"}]}), encoding="utf-8")
    acv._mark_pool_key_exhausted("k1", store=store)
    keys = json.loads(store.read_text(encoding="utf-8"))["keys"]
    if keys[0].get("exhausted_month") != time.strftime("%Y-%m"):
        print("FAIL: key A không được đánh dấu exhausted_month"); bad += 1
    if "exhausted_month" in keys[1]:
        print("FAIL: đánh dấu nhầm key B"); bad += 1
    acv._mark_pool_key_exhausted("k1", store=Path(d) / "missing.json")  # không được ném lỗi
print("test_quota_mark:", "OK" if bad == 0 else f"{bad} FAIL")
sys.exit(1 if bad else 0)
