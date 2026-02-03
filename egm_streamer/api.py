import contextlib
import threading
import time
import shutil
import json
from pathlib import Path
from typing import Optional, Dict, List

from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import AppConfig
from .detector import EgmStateDetector
from .streamer import Streamer
from .models import StreamStatus

# Global references
detector_instance: Optional[EgmStateDetector] = None
streamers: Dict[str, Streamer] = {}
stop_event = threading.Event()

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if detector_instance and detector_instance.config.detector.enabled:
        detect_thread = threading.Thread(target=detection_loop, daemon=True)
        detect_thread.start()
    
    # Snapshot loop startup
    if detector_instance and detector_instance.config.snapshot.enabled:
        snap_thread = threading.Thread(target=snapshot_loop, daemon=True)
        snap_thread.start()

    yield
    # Shutdown
    stop_event.set()
    if detector_instance and 'detect_thread' in locals():
        detect_thread.join(timeout=2.0)
    if detector_instance and 'snap_thread' in locals():
        snap_thread.join(timeout=2.0)

app = FastAPI(lifespan=lifespan)

# Enable CORS for development convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Detection Loop
# -------------------------
def detection_loop():
    global detector_instance
    if not detector_instance:
        return
    
    print("Background detection loop started")
    while not stop_event.is_set():
        detector_instance.step()
        
        t0 = time.time()
        dt = time.time() - t0
        interval = detector_instance.config.detector.capture.capture_interval
        sleep_time = max(0.1, interval - dt)
        
        stop_event.wait(sleep_time)

# -------------------------
# Snapshot Loop
# -------------------------
from .capture import StreamCapturer, StreamConfig as CapturerStreamConfig # Import if needed

def snapshot_loop():
    global detector_instance
    if not detector_instance: return

    snap_cfg = detector_instance.config.snapshot
    print(f"Background snapshot loop started. Target: {snap_cfg.target_stream}, Interval: {snap_cfg.interval}")
    
    # Resolve capture URL
    url = snap_cfg.url
    if not url:
        if snap_cfg.target_stream in detector_instance.config.streams:
            stream_conf = detector_instance.config.streams[snap_cfg.target_stream]
            url = stream_conf.rtmp_url
    
    if not url:
        print("[Snapshot] Error: No URL found for snapshot service")
        return

    # Use default scale/timeout from a StreamConfig
    sc = CapturerStreamConfig(url=url, scale="640:-1", quality=snap_cfg.quality) 
    capturer = StreamCapturer(sc, snap_cfg.output_path)
    
    while not stop_event.is_set():
        try:
            capturer.capture()
            # print(f"[Snapshot] Saved to {snap_cfg.output_path}")
        except Exception as e:
            print(f"[Snapshot] Failed: {e}")
            
        stop_event.wait(snap_cfg.interval)

# -------------------------
# API Endpoints
# -------------------------

@app.get("/api/state")
def get_state():
    """Get current detection state (from status file or instance)"""
    if not detector_instance:
        return {"error": "Detector disabled or not initialized"}
    
    path = detector_instance.config.detector.output.status_file
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e), "state": "UNKNOWN"}

# --- Stream Control ---

@app.get("/api/streams", response_model=Dict[str, StreamStatus])
def get_streams():
    """Get status of all configured streams"""
    return {name: s.status for name, s in streamers.items()}

class StreamControlReq(BaseModel):
    action: str # start, stop, restart

@app.post("/api/streams/{name}/control")
def control_stream(name: str, req: StreamControlReq):
    if name not in streamers:
        raise HTTPException(404, "Stream not found")
    
    s = streamers[name]
    if req.action == "start":
        if not s.status.running:
            s.start()
    elif req.action == "stop":
        s.stop()
    elif req.action == "restart":
        s.stop()
        time.sleep(1)
        s.start()
    else:
        raise HTTPException(400, "Invalid action")
    
    return {"status": "ok", "current": s.status}

# --- Reference Management ---

@app.get("/api/refs/{state}")
def list_refs(state: str):
    """List reference images for a given state"""
    if not detector_instance:
         raise HTTPException(500, "Detector not initialized")
         
    states_cfg = detector_instance.config.detector.states
    if state not in states_cfg:
        raise HTTPException(404, "State not found")
        
    ref_dir = Path(states_cfg[state].refs_dir)
    if not ref_dir.exists():
        return []
        
    # Return list of files relative to ref_dir or just names
    files = sorted([f.name for f in ref_dir.glob("*.jpg")])
    return files

@app.post("/api/refs/{state}")
def add_ref(state: str):
    """Capture current frame and save as reference for state"""
    if not detector_instance:
         raise HTTPException(500, "Detector not initialized")
    
    states_cfg = detector_instance.config.detector.states
    if state not in states_cfg:
        raise HTTPException(404, "State not found")
        
    # Capture NOW
    try:
        tmp_path = detector_instance.capturer.capture()
        
        ref_dir = Path(states_cfg[state].refs_dir)
        ref_dir.mkdir(parents=True, exist_ok=True)
        
        ts = int(time.time())
        filename = f"ref_{ts}.jpg"
        dst = ref_dir / filename
        
        shutil.copy(tmp_path, dst)
        
        # Reload detector refs
        detector_instance.ref_mgr.load_all()
        
        return {"status": "saved", "filename": filename}
    except Exception as e:
        raise HTTPException(500, f"Capture failed: {e}")

@app.get("/api/refs/{state}/{filename}/image")
def get_ref_image(state: str, filename: str):
    if not detector_instance:
         raise HTTPException(500, "Detector not initialized")
         
    states_cfg = detector_instance.config.detector.states
    if state not in states_cfg:
        raise HTTPException(404, "State not found")
        
    ref_dir = Path(states_cfg[state].refs_dir)
    path = ref_dir / filename
    
    if path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(path)
    else:
        raise HTTPException(404, "File not found")

@app.delete("/api/refs/{state}/{filename}")
def delete_ref(state: str, filename: str):
    if not detector_instance:
         raise HTTPException(500, "Detector not initialized")
         
    states_cfg = detector_instance.config.detector.states
    if state not in states_cfg:
        raise HTTPException(404, "State not found")
        
    ref_dir = Path(states_cfg[state].refs_dir)
    path = ref_dir / filename
    
    if path.exists():
        path.unlink()
        # Reload detector refs
        detector_instance.ref_mgr.load_all()
        return {"status": "deleted"}
    else:
        raise HTTPException(404, "File not found")

@app.get("/api/live/frame")
def get_live_frame():
    """Get a single current frame for preview"""
    if not detector_instance:
        raise HTTPException(500, "Detector not initialized")
    
    try:
        # Capture to a preview specific path to avoid overwriting refs
        # Or just use the standard capture method which creates temp files?
        # Capturer usually overwrites a common file or creates new temp?
        # Let's verify StreamCapturer implementation. Assuming it returns a path.
        # For efficiency, we might want to just grab the last captured frame if available?
        # But detector step capture isn't stored publicly.
        # Let's force a capture.
        path = detector_instance.capturer.capture()
        from fastapi.responses import FileResponse
        return FileResponse(path)
    except Exception as e:
        raise HTTPException(500, f"Capture failed: {e}")

# --- Snapshot / Preview ---
# Existing snapshot API modified to be simpler or keep compatibility

class SnapshotReq(BaseModel):
    output_dir: str
    count: int = 1
    interval_ms: int = 0
    prefix: str = "snap"

@app.post("/snapshot/save")
def save_snapshot(req: SnapshotReq):
    # Compatibility endpoint for manual saving
    if not detector_instance:
        raise HTTPException(500, "Detector not initialized")
    
    out = Path(req.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    
    saved = []
    try:
        for i in range(req.count):
            path = detector_instance.capturer.capture()
            ts = int(time.time() * 1000)
            dst = out / f"{req.prefix}_{ts}_{i}.jpg"
            shutil.copy(path, dst)
            saved.append(str(dst))
            
            if i < req.count - 1 and req.interval_ms > 0:
                time.sleep(req.interval_ms / 1000.0)
                
    except Exception as e:
        raise HTTPException(500, f"Snapshot failed: {e}")
        
    return {"saved": len(saved), "files": saved}


# --- Static Files ---
# Serve web UI from 'web' folder inside package
try:
    current_dir = Path(__file__).parent
    web_dir = current_dir / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
except Exception as e:
    print(f"Warning: Could not mount web directory: {e}")


def create_app(config: AppConfig, stream_instances: Dict[str, Streamer] = {}):
    global detector_instance, streamers
    
    if config.detector.enabled:
        # Check if already initialized to check for reload? 
        # For now assume fresh start
        detector_instance = EgmStateDetector(config)
    
    streamers = stream_instances
    return app
