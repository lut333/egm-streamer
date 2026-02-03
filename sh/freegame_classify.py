#!/opt/freegame-venv/bin/python
import argparse, io, json, os, sys, time, glob, subprocess
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime, timezone

from PIL import Image
import imagehash

# -------------------------
# helpers
# -------------------------
def load_img_gray_retry(path: str, tries: int = 5, sleep_s: float = 0.02) -> Image.Image:
    last = None
    for _ in range(tries):
        try:
            with open(path, "rb") as f:
                data = f.read()
            img = Image.open(io.BytesIO(data))
            img.load()  # 強制完整解碼，避免半張圖
            return img.convert("L")
        except Exception as e:
            last = e
            time.sleep(sleep_s)
    raise last

def ts_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_roi(s: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    if not s:
        return None
    x, y, w, h = [int(v) for v in s.split(",")]
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
    # 先讀整個檔案，降低讀到半張的風險
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

def best_k_mean(dists: List[int], k: int) -> float:
    if not dists:
        return 9999.0
    ds = sorted(dists)
    k = max(1, min(k, len(ds)))
    best = ds[:k]
    return sum(best) / len(best)

def latest_mtime_in_dir(ref_path: str) -> int:
    p = Path(ref_path)
    mt = 0
    if p.is_file():
        try:
            return int(p.stat().st_mtime)
        except:
            return 0
    if not p.is_dir():
        return 0
    for f in p.glob("*"):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            try:
                mt = max(mt, int(f.stat().st_mtime))
            except:
                pass
    return mt

def min_dist(cur_hash, ref_hashes) -> int:
    # ref_hashes: List[ImageHash]
    return min(int(cur_hash - rh) for rh in ref_hashes)

def read_prev(out_path: str) -> tuple[str, int, int, int]:
    prev_state = "NONE"
    ss = ps = ns = 0
    try:
        with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
        for line in reversed(lines):
            if "state=" not in line:
                continue
            for part in line.split():
                if part.startswith("state="):
                    prev_state = part.split("=", 1)[1].strip() or "NONE"
                elif part.startswith("select_streak="):
                    try: ss = int(part.split("=", 1)[1])
                    except: ss = 0
                elif part.startswith("play_streak="):
                    try: ps = int(part.split("=", 1)[1])
                    except: ps = 0
                elif part.startswith("none_streak="):
                    try: ns = int(part.split("=", 1)[1])
                    except: ns = 0
            break
    except Exception:
        pass
    return prev_state, ss, ps, ns
def decide_state(prev_state: str,
                 obs_select: bool, obs_play: bool,
                 sel_mean: float, play_mean: float,
                 prev_ss: int, prev_ps: int, prev_ns: int,
                 confirm_select: int, confirm_play: int,
                 drop_select_none: int, drop_play_none: int):

    # 若兩者都命中：在 NONE/SELECT 階段一律優先 SELECT（避免跳過選擇畫面）
    if obs_select and obs_play and prev_state in ("NONE", "SELECT"):
        obs_play = False

    obs_none = (not obs_select) and (not obs_play)

    ss = prev_ss + 1 if obs_select else 0
    ps = prev_ps + 1 if obs_play else 0

    # ns 的意義：
    # - 在 PLAY：代表「連續幾輪沒有 obs_play」（用來退出 PLAY）
    # - 其他狀態：代表「連續 NONE」
    if prev_state == "PLAY":
        ns = prev_ns + 1 if (not obs_play) else 0
    else:
        ns = prev_ns + 1 if obs_none else 0

    state = prev_state

    if prev_state == "NONE":
        # 禁止 NONE → PLAY，只能先進 SELECT
        state = "SELECT" if ss >= confirm_select else "NONE"

    elif prev_state == "SELECT":
        # SELECT → PLAY 需要確認命中
        if ps >= confirm_play:
            state = "PLAY"
        elif obs_select:
            state = "SELECT"
        else:
            state = "NONE" if ns >= drop_select_none else "SELECT"

    elif prev_state == "PLAY":
        # PLAY 只看「play 是否持續命中」來維持，連續沒命中就退出
        if obs_play:
            state = "PLAY"
        else:
            state = "NONE" if ns >= drop_play_none else "PLAY"

    else:
        state = "NONE"

    #return state, ss, ps, ns, obs_select, obs_play, (state == "NONE")
    return state, ss, ps, ns, obs_select, obs_play, obs_none


# -------------------------
# refhash cache loading / validation
# -------------------------
def load_refhash_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def validate_cache(cache: dict, expected_roi, algo: str, hash_size: int,
                   expected_ref_path: str, expected_latest_mtime: int) -> Optional[str]:
    # ROI/Algo/HashSize 要一致
    if cache.get("algo") != algo:
        return f"algo mismatch cache={cache.get('algo')} expected={algo}"
    if int(cache.get("hash_size", -1)) != int(hash_size):
        return f"hash_size mismatch cache={cache.get('hash_size')} expected={hash_size}"

    roi = cache.get("roi") or {}
    if (int(roi.get("x", -1)), int(roi.get("y", -1)), int(roi.get("w", -1)), int(roi.get("h", -1))) != expected_roi:
        return f"roi mismatch cache={roi} expected={expected_roi}"

    # refs 路徑不一定要相同（可能另外搬資料夾），所以這項只做弱檢查
    # 但 mtime 必須不落後
    latest = cache.get("refs_latest_mtime_epoch") or {}
    # build_refhash.py 會分 select / play 記錄
    # 這裡不猜 key，直接用 expected_latest_mtime 對比 cache 最大全部值
    cache_mt = 0
    try:
        if isinstance(latest, dict):
            cache_mt = max(int(v) for v in latest.values() if v is not None)
    except:
        cache_mt = 0

    if expected_latest_mtime and cache_mt and cache_mt < expected_latest_mtime:
        return f"refs updated (cache_mtime={cache_mt} < refs_mtime={expected_latest_mtime})"

    return None

def to_hash_list(items: list) -> List:
    hs = []
    for it in items:
        h = it.get("hash")
        if not h:
            continue
        hs.append(imagehash.hex_to_hash(h))
    return hs

def rebuild_cache(build_refhash_path: str, args, roi_select, roi_play1, roi_play2) -> None:
    cmd = [
        build_refhash_path,
        "--ref-select", args.ref_select,
        "--ref-play", args.ref_play,
        "--roi-select", f"{roi_select[0]},{roi_select[1]},{roi_select[2]},{roi_select[3]}",
        "--roi-play1",  f"{roi_play1[0]},{roi_play1[1]},{roi_play1[2]},{roi_play1[3]}",
        "--algo", args.algo,
        "--hash-size", str(args.hash_size),
        "--out-dir", args.refhash_dir,
        "--out-prefix", args.refhash_prefix,
    ]
    if roi_play2:
        cmd += ["--roi-play2", f"{roi_play2[0]},{roi_play2[1]},{roi_play2[2]},{roi_play2[3]}"]

    # 用 subprocess 跑一次 build_refhash
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"build_refhash failed rc={r.returncode} output:\n{r.stdout}")

def load_or_build_refhashes(args, roi_select, roi_play1, roi_play2):
    # cache files
    p_sel  = str(Path(args.refhash_dir) / f"{args.refhash_prefix}_select.json")
    p_p1   = str(Path(args.refhash_dir) / f"{args.refhash_prefix}_play1.json")
    p_p2   = str(Path(args.refhash_dir) / f"{args.refhash_prefix}_play2.json")

    # 判斷 refs 是否更新（用最新 mtime）
    sel_latest = latest_mtime_in_dir(args.ref_select)
    play_latest = latest_mtime_in_dir(args.ref_play)

    def try_load():
        sel_cache = load_refhash_json(p_sel)
        p1_cache  = load_refhash_json(p_p1)
        p2_cache  = load_refhash_json(p_p2) if (roi_play2 and os.path.isfile(p_p2)) else None

        err = validate_cache(sel_cache, roi_select, args.algo, args.hash_size, args.ref_select, sel_latest)
        if err: return None, f"select cache invalid: {err}"
        err = validate_cache(p1_cache, roi_play1, args.algo, args.hash_size, args.ref_play, play_latest)
        if err: return None, f"play1 cache invalid: {err}"
        if roi_play2 and p2_cache:
            err = validate_cache(p2_cache, roi_play2, args.algo, args.hash_size, args.ref_play, play_latest)
            if err: return None, f"play2 cache invalid: {err}"

        sel_refs = to_hash_list(sel_cache.get("items") or [])
        p1_refs  = to_hash_list(p1_cache.get("items") or [])
        p2_refs  = to_hash_list(p2_cache.get("items") or []) if p2_cache else []

        if not sel_refs:
            return None, "select cache has 0 hashes"
        if not p1_refs:
            return None, "play1 cache has 0 hashes"

        return (sel_refs, p1_refs, p2_refs), None

    # 先嘗試直接載入
    loaded, err = None, None
    if os.path.isfile(p_sel) and os.path.isfile(p_p1):
        loaded, err = try_load()
        if loaded:
            return loaded, "cache_ok"

    if not args.auto_rebuild_cache:
        raise RuntimeError(f"refhash cache missing/invalid and auto_rebuild_cache=0. reason={err or 'missing'}")

    # 自動重建
    build_refhash_path = args.build_refhash
    if not build_refhash_path:
        build_refhash_path = "/usr/local/bin/build_refhash.py"
    if not os.path.isfile(build_refhash_path):
        raise RuntimeError(f"need rebuild but build_refhash not found: {build_refhash_path}")

    rebuild_cache(build_refhash_path, args, roi_select, roi_play1, roi_play2)

    # 重建後再載入
    loaded, err2 = try_load()
    if not loaded:
        raise RuntimeError(f"rebuilt but still invalid: {err2}")
    return loaded, "cache_rebuilt"

# -------------------------
# main
# -------------------------
def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--snap", default="/dev/shm/freegame/snap.jpg")
    ap.add_argument("--out", default="/dev/shm/freegame/freegame.status")

    # refs path（用來判斷 refs 是否更新）
    ap.add_argument("--ref-select", required=True, help="SELECT refs folder or file")
    ap.add_argument("--ref-play", required=True, help="PLAY refs folder or file")

    # ROI（必須與 build_refhash 一致）
    ap.add_argument("--roi-select", required=True, help="x,y,w,h")
    ap.add_argument("--roi-play1", required=True, help="x,y,w,h")
    ap.add_argument("--roi-play2", default=None, help="x,y,w,h (optional)")

    # thresholds
    ap.add_argument("--select-enter", type=int, default=12)
    ap.add_argument("--select-exit", type=int, default=16)
    ap.add_argument("--play-enter", type=int, default=12)
    ap.add_argument("--play-exit", type=int, default=16)

    # sampling
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--interval", type=float, default=0.15)
    ap.add_argument("--bestk", type=int, default=0, help="0 => auto (=need)")

    # hash settings（必須與 build_refhash 一致）
    ap.add_argument("--algo", default="dhash", choices=["phash", "dhash", "ahash"])
    ap.add_argument("--hash-size", type=int, default=8)

    # debounce
    ap.add_argument("--confirm-select", type=int, default=2)
    ap.add_argument("--confirm-play", type=int, default=2)
    ap.add_argument("--drop-select-none", type=int, default=6)
    ap.add_argument("--drop-play-none", type=int, default=6)

    # cache settings
    ap.add_argument("--refhash-dir", default="/dev/shm/freegame")
    ap.add_argument("--refhash-prefix", default="refhash")
    ap.add_argument("--auto-rebuild-cache", type=int, default=1, help="1: missing/invalid cache -> rebuild via build_refhash")
    ap.add_argument("--build-refhash", default="", help="path to build_refhash.py (optional)")

    args = ap.parse_args()

    roi_select = parse_roi(args.roi_select)
    roi_play1  = parse_roi(args.roi_play1)
    roi_play2  = parse_roi(args.roi_play2) if args.roi_play2 else None

    if not os.path.isfile(args.snap):
        atomic_write(args.out, f"ts={ts_iso()} epoch={int(time.time())} state=UNKNOWN reason=snap_missing\n")
        print("UNKNOWN snap_missing")
        sys.exit(2)

    # read prev state
    prev_state, prev_ss, prev_ps, prev_ns = read_prev(args.out)

    # hysteresis threshold
    th_select = args.select_exit if prev_state == "SELECT" else args.select_enter
    th_play = args.play_exit if prev_state == "PLAY" else args.play_enter

    need = (args.samples // 2) + 1
    k = args.bestk if args.bestk > 0 else need
    k = max(1, min(k, args.samples))

    # load ref hashes from /dev/shm (or rebuild)
    try:
        (sel_refs, play1_refs, play2_refs), cache_status = load_or_build_refhashes(
            args, roi_select, roi_play1, roi_play2
        )
    except Exception as e:
        atomic_write(args.out, f"ts={ts_iso()} epoch={int(time.time())} state=UNKNOWN reason=refhash_failed err={str(e).replace(' ','_')}\n")
        print(f"UNKNOWN refhash_failed: {e}")
        sys.exit(2)

    # sample loop
    dsel: List[int] = []
    dplay1: List[int] = []
    dplay2: List[int] = []

    for i in range(args.samples):
        try:
            full = load_img_gray_retry(args.snap)

            hs  = get_hash(crop(full, roi_select), args.algo, args.hash_size)
            hp1 = get_hash(crop(full, roi_play1),  args.algo, args.hash_size)

            dsel.append(min_dist(hs, sel_refs))
            dplay1.append(min_dist(hp1, play1_refs))

            if play2_refs and roi_play2:
                hp2 = get_hash(crop(full, roi_play2), args.algo, args.hash_size)
                dplay2.append(min_dist(hp2, play2_refs))

        except Exception:
            atomic_write(args.out, f"ts={ts_iso()} epoch={int(time.time())} state=UNKNOWN reason=image_decode_failed\n")
            print("UNKNOWN image_decode_failed")
            sys.exit(2)

        if i < args.samples - 1:
            time.sleep(args.interval)
        
       

    # best-k mean distances
    sel_mean = best_k_mean(dsel, k)
    play_mean1 = best_k_mean(dplay1, k)
    play_mean2 = best_k_mean(dplay2, k) if dplay2 else 9999.0
    play_mean = min(play_mean1, play_mean2)

    # observations
    obs_select = sel_mean <= th_select
    obs_play = play_mean <= th_play
    obs_none = (not obs_select) and (not obs_play)

    # decide by streak
    state, ss, ps, ns, obs_select, obs_play, obs_none = decide_state(
        prev_state=prev_state,
        obs_select=obs_select,
        obs_play=obs_play,
        sel_mean=sel_mean,
        play_mean=play_mean,
        prev_ss=prev_ss,
        prev_ps=prev_ps,
        prev_ns=prev_ns,
        confirm_select=max(1, args.confirm_select),
        confirm_play=max(1, args.confirm_play),
        drop_select_none=max(1, args.drop_select_none),
        drop_play_none=max(1, args.drop_play_none),
    )

    epoch = int(time.time())
    line = (
        f"ts={ts_iso()} epoch={epoch} state={state} cache={cache_status} "
        f"obs_select={int(obs_select)} obs_play={int(obs_play)} obs_none={int(obs_none)} "
        f"select_mean={sel_mean:.2f} select_th={th_select} select_list={','.join(map(str,dsel))} "
        f"play_mean={play_mean:.2f} play_th={th_play} play1_mean={play_mean1:.2f} play2_mean={play_mean2:.2f} "
        f"play1_list={','.join(map(str,dplay1))} play2_list={','.join(map(str,dplay2)) if dplay2 else '-'} "
        f"bestk={k} algo={args.algo} hash_size={args.hash_size} "
        f"confirm_select={args.confirm_select} confirm_play={args.confirm_play} "
        f"drop_select_none={args.drop_select_none} drop_play_none={args.drop_play_none} "
        f"select_streak={ss} play_streak={ps} none_streak={ns} "
        f"refs_select={len(sel_refs)} refs_play1={len(play1_refs)} refs_play2={len(play2_refs) if play2_refs else 0}\n"
    )
    atomic_write(args.out, line)

    if state == "PLAY":
        print("FREEGAME=PLAY")
        sys.exit(0)
    elif state == "SELECT":
        print("FREEGAME=SELECT")
        sys.exit(0)
    else:
        print("FREEGAME=NO")
        sys.exit(1)

if __name__ == "__main__":
    main()