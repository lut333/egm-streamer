#!/opt/freegame-venv/bin/python
import argparse
import glob
import io
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from PIL import Image
import imagehash


def ts_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_roi(s: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    if not s:
        return None
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise ValueError("ROI must be x,y,w,h")
    x, y, w, h = [int(v) for v in parts]
    if w <= 0 or h <= 0:
        raise ValueError("ROI w/h must be > 0")
    return (x, y, w, h)


def crop(img: Image.Image, roi: Optional[Tuple[int, int, int, int]]) -> Image.Image:
    if not roi:
        return img
    x, y, w, h = roi
    return img.crop((x, y, x + w, y + h))


def atomic_write(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_img_gray(path: str) -> Image.Image:
    with open(path, "rb") as f:
        data = f.read()
    img = Image.open(io.BytesIO(data))
    return img.convert("L")


def get_hash(img: Image.Image, algo: str, hash_size: int):
    if algo == "phash":
        return imagehash.phash(img, hash_size=hash_size)
    if algo == "dhash":
        return imagehash.dhash(img, hash_size=hash_size)
    if algo == "ahash":
        return imagehash.average_hash(img, hash_size=hash_size)
    raise ValueError("algo must be one of: phash, dhash, ahash")


def collect_files(ref_path: str) -> List[str]:
    if os.path.isfile(ref_path):
        return [ref_path]
    if os.path.isdir(ref_path):
        cand = sorted(glob.glob(os.path.join(ref_path, "*")))
        return [
            p for p in cand
            if os.path.isfile(p) and p.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ]
    return []


def latest_mtime_in_dir(ref_path: str) -> int:
    p = Path(ref_path)
    if p.is_file():
        try:
            return int(p.stat().st_mtime)
        except:
            return 0
    if not p.is_dir():
        return 0
    mt = 0
    for f in p.glob("*"):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            try:
                mt = max(mt, int(f.stat().st_mtime))
            except:
                pass
    return mt


def build_items(files: List[str], roi, algo: str, hash_size: int) -> List[dict]:
    items = []
    for fp in files:
        try:
            img = crop(load_img_gray(fp), roi)
            h = get_hash(img, algo, hash_size)
            mtime = 0
            try:
                mtime = int(Path(fp).stat().st_mtime)
            except:
                mtime = 0
            items.append({
                "file": fp,
                "mtime_epoch": mtime,
                "hash": str(h),  # hex string
            })
        except Exception as e:
            # 不中斷整批，跳過壞檔
            items.append({
                "file": fp,
                "mtime_epoch": 0,
                "hash": "",
                "error": str(e),
            })
    # 濾掉失敗的
    items = [it for it in items if it.get("hash")]
    return items


def write_cache(path: str,
                roi_name: str,
                roi,
                algo: str,
                hash_size: int,
                ref_select_path: str,
                ref_play_path: str,
                sel_latest: int,
                play_latest: int,
                items: List[dict]) -> None:
    x, y, w, h = roi
    doc = {
        "ts": ts_iso(),
        "algo": algo,
        "hash_size": int(hash_size),
        "roi": {"name": roi_name, "x": int(x), "y": int(y), "w": int(w), "h": int(h)},
        # 讓 classify 那邊可以用來判斷 refs 是否更新
        "refs_latest_mtime_epoch": {
            "select": int(sel_latest),
            "play": int(play_latest),
        },
        "refs_path": {
            "ref_select": ref_select_path,
            "ref_play": ref_play_path,
        },
        "items": items,
        "count": len(items),
    }
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--ref-select", required=True, help="SELECT refs folder or file")
    ap.add_argument("--ref-play", required=True, help="PLAY refs folder or file")

    ap.add_argument("--roi-select", required=True, help="x,y,w,h")
    ap.add_argument("--roi-play1", required=True, help="x,y,w,h")
    ap.add_argument("--roi-play2", default="", help="x,y,w,h (optional)")

    ap.add_argument("--algo", default="dhash", choices=["phash", "dhash", "ahash"])
    ap.add_argument("--hash-size", type=int, default=8)

    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--out-prefix", default="refhash")

    args = ap.parse_args()

    roi_select = parse_roi(args.roi_select)
    roi_play1 = parse_roi(args.roi_play1)
    roi_play2 = parse_roi(args.roi_play2) if args.roi_play2 else None

    sel_files = collect_files(args.ref_select)
    play_files = collect_files(args.ref_play)

    if not sel_files:
        print(f"[ERROR] no valid select refs: {args.ref_select}", file=sys.stderr)
        sys.exit(2)
    if not play_files:
        print(f"[ERROR] no valid play refs: {args.ref_play}", file=sys.stderr)
        sys.exit(2)

    sel_latest = latest_mtime_in_dir(args.ref_select)
    play_latest = latest_mtime_in_dir(args.ref_play)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    p_sel = str(out_dir / f"{args.out_prefix}_select.json")
    p_p1  = str(out_dir / f"{args.out_prefix}_play1.json")
    p_p2  = str(out_dir / f"{args.out_prefix}_play2.json")

    sel_items = build_items(sel_files, roi_select, args.algo, args.hash_size)
    p1_items  = build_items(play_files, roi_play1, args.algo, args.hash_size)
    p2_items  = build_items(play_files, roi_play2, args.algo, args.hash_size) if roi_play2 else []

    if not sel_items:
        print("[ERROR] select refs all failed to hash", file=sys.stderr)
        sys.exit(2)
    if not p1_items:
        print("[ERROR] play1 refs all failed to hash", file=sys.stderr)
        sys.exit(2)

    write_cache(p_sel, "select", roi_select, args.algo, args.hash_size,
                args.ref_select, args.ref_play, sel_latest, play_latest, sel_items)

    write_cache(p_p1, "play1", roi_play1, args.algo, args.hash_size,
                args.ref_select, args.ref_play, sel_latest, play_latest, p1_items)

    if roi_play2:
        if not p2_items:
            print("[WARN] play2 roi set but no hashes produced; skip play2 cache", file=sys.stderr)
        else:
            write_cache(p_p2, "play2", roi_play2, args.algo, args.hash_size,
                        args.ref_select, args.ref_play, sel_latest, play_latest, p2_items)

    print(f"OK: {p_sel}")
    print(f"OK: {p_p1}")
    if roi_play2 and p2_items:
        print(f"OK: {p_p2}")


if __name__ == "__main__":
    main()