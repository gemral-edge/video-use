"""Deliver a finished CUT video to Jennie's Google Drive (local half) — SKILL.md steps 8/8b.

Why a script (MCP-FIRST case c): the Drive MCP `create_file` cannot upload a several-hundred-MB
video, so the file goes through the Drive for desktop mount (J:). This script does everything
that needs the local disk and NOTHING that needs the cloud:

  1. make sure J:\\My Drive is mounted (start GoogleDriveFS.exe if not, wait up to 60 s)
  2. build folder/file names that fit Drive's 255-byte UTF-8 limit (cut at the first comma)
  3. create <root>\\<year>\\<TITLE>\\ and copy the video there as CUT_<TITLE>.mp4
     (skip when a same-size copy is already there; --force overwrites)
  4. wait until the size seen on the mount == source size and stays put for 2 polls
     (right after copyfile the mount can show a PARTIAL size — seen 19/11: 781.189.120 vs 781.702.759)
  5. print the exact Drive-MCP checks the agent must still run. Cloud confirmation is NOT done
     here: "delivered" = cloud fileSize == local size AND modifiedTime >= the stamp printed below.

Exit: 0 ok | 2 Drive not mounted | 3 size never settled | 4 bad input.

Usage:
  python helpers/deliver_drive.py <video.mp4> [--title "<TÊN VIDEO>"] [--year 2026] [--force]
  python helpers/deliver_drive.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

DEFAULT_ROOT = os.path.join("J:" + os.sep, "My Drive", "Social Media Jennie",
                            "Footage Tổng Hợp", "YOUTUBE")
# Drive folder ids of the year folders (for the MCP check lines). Add a year = 1 entry.
YEAR_FOLDER_IDS = {"2026": "1u0GDjEGW2e0RjiLXWNsTxrpqXHXMscrt"}
DRIVE_FS_GLOB = r"C:\Program Files\Google\Drive File Stream\*\GoogleDriveFS.exe"
MAX_BYTES = 255
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


SHORT_NAME = re.compile(r"~\d")  # Windows 8.3 short-name marker, e.g. NMVTRS~1


def long_path(p: str) -> str:
    """Expand 8.3 short names (D:\\...\\NMVTRS~1\\CC_NMV~1.MP4) to the real long path.

    19/11 incident: a short path was passed in, the folder title was taken from it, and a stray
    'NMVTRS~1' folder + a 781 MB duplicate landed on Jennie's Drive."""
    if os.name != "nt":
        return p
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(p, buf, len(buf))
        return buf.value if 0 < n < len(buf) else p
    except Exception:
        return p


def _fits(title: str) -> bool:
    # the file name is the longer one: "CUT_" + title + ".mp4"
    return len(title.encode("utf-8")) <= MAX_BYTES and len(("CUT_" + title + ".mp4").encode("utf-8")) <= MAX_BYTES


def clean_title(raw: str) -> str:
    """Drop characters Windows/Drive refuse, collapse spaces, trim trailing dots/spaces."""
    t = ILLEGAL.sub(" ", raw)
    t = re.sub(r"\s+", " ", t).strip().rstrip(".")
    return t


def fit_title(raw: str) -> str:
    """Return a title whose folder AND `CUT_<title>.mp4` are <= 255 UTF-8 bytes.

    Long titles are cut at the FIRST comma (what Jennie asked for 13/11); if that is still too
    long, cut at a word boundary byte-safely."""
    t = clean_title(raw)
    if _fits(t):
        return t
    if "," in t:
        head = t.split(",", 1)[0].strip()
        if head and _fits(head):
            return head
        t = head or t
    words, out = t.split(" "), ""
    for w in words:
        cand = (out + " " + w).strip()
        if not _fits(cand):
            break
        out = cand
    if not out:  # one giant word: cut by bytes, never inside a UTF-8 character
        out = t.encode("utf-8")[: MAX_BYTES - len("CUT_.mp4")].decode("utf-8", "ignore")
    return out.strip()


def _mount_dir(root: str):
    """The 'My Drive' ancestor of root (the thing that exists only while Drive is mounted)."""
    p = root
    while True:
        if os.path.basename(p) == "My Drive":
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def ensure_mounted(root: str, wait_s: int = 60) -> bool:
    mount = _mount_dir(root)
    if mount is None:  # custom --root without a 'My Drive' part: only require its drive to exist
        return os.path.isdir(os.path.splitdrive(root)[0] + os.sep)
    if os.path.isdir(mount):
        return True
    exes = sorted(glob.glob(DRIVE_FS_GLOB), key=lambda p: [int(x) if x.isdigit() else 0
                                                           for x in re.split(r"\D+", os.path.dirname(p))])
    if not exes:
        return False
    # Drive for desktop is a GUI exe; a detached start does not flash a console window.
    subprocess.Popen([exes[-1]], creationflags=0x08000000 | 0x00000008 if os.name == "nt" else 0)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if os.path.isdir(mount):
            return True
        time.sleep(2)
    return False


def wait_size_settled(path: str, want: int, timeout_s: int, poll_s: float = 3.0) -> int:
    """Poll the mount until size == want for 2 consecutive polls. Returns the last size seen."""
    t0, ok, last = time.time(), 0, -1
    while time.time() - t0 < timeout_s:
        try:
            last = os.path.getsize(path)
        except OSError:
            last = -1
        ok = ok + 1 if last == want else 0
        if ok >= 2:
            return last
        time.sleep(poll_s)
    return last


def deliver(src: str, title: str, year: str, root: str, force: bool, settle_s: int, poll_s: float,
            dry_run: bool = False):
    if not os.path.isfile(src):
        print(f"❌ không thấy file nguồn: {src}")
        return 4
    title = fit_title(title)
    if not title:
        print("❌ tên video rỗng sau khi làm sạch")
        return 4
    if SHORT_NAME.search(title):
        print(f"❌ tên '{title}' có dạng tên ngắn Windows (~1) — không tạo gì trên Drive. Truyền --title \"<TÊN VIDEO đầy đủ>\"")
        return 4
    folder = os.path.join(root, year, title)
    dst = os.path.join(folder, f"CUT_{title}.mp4")
    want = os.path.getsize(src)
    print(f"folder : {folder}  ({len(title.encode('utf-8'))} byte)")
    print(f"file   : CUT_{title}.mp4  ({len(('CUT_' + title + '.mp4').encode('utf-8'))} byte)")
    if dry_run:
        has = os.path.isfile(dst)
        print(f"DRY-RUN: không tạo gì. Đích {'ĐÃ có' if has else 'chưa có'}"
              + (f" ({os.path.getsize(dst):,} byte, nguồn {want:,})" if has else "") + f" -> {dst}")
        return 0
    if not ensure_mounted(root):
        print("❌ ổ J: (Google Drive) chưa gắn và không bật được GoogleDriveFS.exe — mở Drive for desktop rồi chạy lại")
        return 2
    os.makedirs(folder, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if os.path.isfile(dst) and os.path.getsize(dst) == want and not force:
        print(f"= đã có bản cùng dung lượng ({want:,} byte) — bỏ qua copy (--force để ghi đè)")
        stamp = None
    else:
        t0 = time.time()
        shutil.copyfile(src, dst)
        print(f"copy xong {want:,} byte trong {time.time() - t0:.0f} s")
    got = wait_size_settled(dst, want, settle_s, poll_s)
    if got != want:
        print(f"❌ size trên ổ J: không ổn định về {want:,} (cuối cùng đọc được {got:,}) — Drive còn ghi dở, chạy lại sau")
        return 3
    print(f"✅ ổ J: ổn định {got:,} byte")
    print("\nCÒN LẠI (agent, Drive MCP — KHÔNG phải script): báo 'đã giao' chỉ khi cả 3 đúng")
    key = title[:40].replace("'", "\\'")
    yid = YEAR_FOLDER_IDS.get(year, "<id thư mục năm>")
    print(f"  1. search_files: title contains '{key}' and parentId = '{yid}'   -> id thư mục + viewUrl")
    print(f"  2. search_files: parentId = '<id thư mục>'                          -> file")
    print(f"  3. fileSize == {want}" + (f"  và modifiedTime >= {stamp}" if stamp else "  (không ghi đè lần này nên không cần modifiedTime mới)"))
    print("JSON " + json.dumps({"dst": dst, "size": want, "title": title, "year": year,
                                "copied_at_utc": stamp}, ensure_ascii=False))
    return 0


def selftest() -> int:
    bad = 0

    def chk(name, cond):
        nonlocal bad
        print(("PASS " if cond else "FAIL ") + name)
        bad += 0 if cond else 1

    short = "Thực Hành Manifest"
    chk("tên ngắn giữ nguyên", fit_title(short) == short)
    long_t = ("DỰ BÁO TÂM LINH 2027 SỰ KIỆN THIÊN VĂN HUYỀN BÍ ĐIỂM KHÔNG ĐỘ VŨ TRỤ " * 4).strip() + ", phần sau dấu phẩy"
    f = fit_title(long_t)
    chk("tên dài vừa 255 byte (cả CUT_….mp4)", _fits(f))
    cm = ("MỞ KHÓA CỔNG LƯỢNG TỬ ĐẠI " * 4).strip() + ", " + "x" * 200
    chk("cắt ở dấu phẩy đầu", "," not in fit_title(cm) and fit_title(cm).endswith("ĐẠI"))
    giant = "Ệ" * 200  # 3 byte mỗi ký tự, không dấu cách, không dấu phẩy
    g = fit_title(giant)
    chk("một từ khổng lồ: cắt theo byte, không vỡ UTF-8", _fits(g) and g == g.encode("utf-8").decode("utf-8") and len(g) > 0)
    chk("ký tự cấm bị bỏ", fit_title('a:b*c?"d') == "a b c d")
    # real copy into a temp 'drive'
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "My Drive"))
        src = os.path.join(tmp, "v.mp4")
        with open(src, "wb") as fh:
            fh.write(os.urandom(2_000_000))
        root = os.path.join(tmp, "My Drive", "YT")
        rc1 = deliver(src, "Thử nghiệm", "2026", root, False, 10, 0.2)
        dst = os.path.join(root, "2026", "Thử nghiệm", "CUT_Thử nghiệm.mp4")
        chk("copy thật vào ổ giả", rc1 == 0 and os.path.getsize(dst) == 2_000_000)
        m1 = os.path.getmtime(dst)
        time.sleep(0.05)
        rc2 = deliver(src, "Thử nghiệm", "2026", root, False, 10, 0.2)
        chk("chạy lại: bỏ qua copy khi cùng dung lượng", rc2 == 0 and os.path.getmtime(dst) == m1)
        with open(dst, "wb") as fh:  # simulate a partial copy
            fh.write(b"x" * 1000)
        rc3 = deliver(src, "Thử nghiệm", "2026", root, False, 10, 0.2)
        chk("dung lượng lệch -> copy lại và khớp", rc3 == 0 and os.path.getsize(dst) == 2_000_000)
        rc4 = wait_size_settled(os.path.join(tmp, "nope.mp4"), 5, 1, 0.2)
        chk("file không tồn tại -> không báo ổn định", rc4 != 5)
        # 19/11 incident guard: an 8.3 title must be refused and must create NOTHING
        before = set(os.listdir(os.path.join(root, "2026")))
        rc5 = deliver(src, "NMVTRS~1", "2026", root, False, 10, 0.2)
        chk("tên dạng 8.3 (~1) bị từ chối, không tạo thư mục", rc5 == 4 and set(os.listdir(os.path.join(root, "2026"))) == before)
        # long_path really expands a short path (only where Windows made one)
        if os.name == "nt":
            import ctypes
            longdir = os.path.join(tmp, "Thư mục có tên rất dài")
            os.makedirs(longdir)
            lf = os.path.join(longdir, "Tệp video dài.mp4")
            open(lf, "wb").close()
            buf = ctypes.create_unicode_buffer(1024)
            ctypes.windll.kernel32.GetShortPathNameW(lf, buf, 1024)
            tail = os.path.join("Thư mục có tên rất dài", "Tệp video dài.mp4")
            if "~" in buf.value:
                got = long_path(buf.value)
                chk("long_path đổi tên ngắn về tên dài", "~" not in got and got.endswith(tail))
            else:
                print("SKIP long_path (ổ này không tạo tên 8.3)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("selftest:", "OK" if bad == 0 else f"{bad} FAIL")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Deliver a CUT video to Drive via the J: mount (local half)")
    ap.add_argument("video", nargs="?")
    ap.add_argument("--title", help="tên video (mặc định: tên thư mục chứa edit/, hoặc tên file bỏ CUT_)")
    ap.add_argument("--year", default=str(dt.date.today().year))
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--force", action="store_true", help="ghi đè dù đã có bản cùng dung lượng")
    ap.add_argument("--settle", type=int, default=600, help="giây tối đa chờ size ổn định (mặc định 600)")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in tên thư mục/tệp sẽ tạo, không tạo/copy gì")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.video:
        ap.error("cần đường dẫn file video (hoặc --selftest)")
    src = long_path(os.path.abspath(a.video))
    title = a.title
    if not title:
        parent = os.path.basename(os.path.dirname(os.path.dirname(src)))  # <videos_dir>/edit/CUT.mp4
        title = parent if os.path.basename(os.path.dirname(src)).lower() == "edit" and parent else \
            re.sub(r"^CUT_", "", os.path.splitext(os.path.basename(src))[0])
    return deliver(src, title, a.year, a.root, a.force, a.settle, 3.0, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
