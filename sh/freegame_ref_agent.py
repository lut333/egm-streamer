#!/opt/freegame-venv/bin/python
import os, time, io
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from PIL import Image


APP = FastAPI()


SNAP_PATH = "/dev/shm/freegame/snap.jpg"
BASE_DIR  = "/home/dls/game_ref"
TOKEN_ENV = "REF_AGENT_TOKEN"

def require_token(x_token: Optional[str]):
  token = os.getenv(TOKEN_ENV, "")
  if token and x_token != token:
    raise HTTPException(status_code=401, detail="bad token")

def safe_read_bytes(path: str) -> bytes:
  with open(path, "rb") as f:
    data = f.read()
  if not data:
    raise ValueError("empty snap")
  img = Image.open(io.BytesIO(data))
  img.verify()
  return data

def atomic_write(dst: Path, data: bytes):
  dst.parent.mkdir(parents=True, exist_ok=True)
  tmp = dst.with_suffix(dst.suffix + ".tmp")
  with open(tmp, "wb") as f:
    f.write(data)
    f.flush()
    os.fsync(f.fileno())
  os.replace(tmp, dst)

class GrabReq(BaseModel):
  dest: str                # ref_selects / ref_plays
  prefix: str = "ref"      # 檔名前綴
  count: int = 15
  interval_ms: int = 200
  limit: int = 200

@APP.post("/v1/refs/grab")
def grab(req: GrabReq, x_token: Optional[str] = Header(default=None)):
  require_token(x_token)

  if req.count < 1:
    raise HTTPException(400, "count < 1")
  if req.count > req.limit:
    raise HTTPException(400, f"count > limit ({req.limit})")

  snap = Path(SNAP_PATH)
  if not snap.exists():
    raise HTTPException(404, "snap not found")

  dest_dir = Path(BASE_DIR) / req.dest
  dest_dir.mkdir(parents=True, exist_ok=True)

  ts = datetime.now().strftime("%Y%m%d_%H%M%S")
  saved = 0

  for i in range(1, req.count + 1):
    data = safe_read_bytes(SNAP_PATH)
    name = f"{req.prefix}_{ts}_{i:02d}.jpg"
    atomic_write(dest_dir / name, data)
    saved += 1
    if i < req.count and req.interval_ms > 0:
      time.sleep(req.interval_ms / 1000.0)

  return {"ok": True, "saved": saved, "dir": str(dest_dir)}