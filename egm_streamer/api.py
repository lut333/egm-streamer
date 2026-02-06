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
from .capture import StreamCapturer, PersistentCapturer, StreamConfig as CapturerStreamConfig

# Global references
detector_instance: Optional[EgmStateDetector] = None
app_config: Optional[AppConfig] = None
streamers: Dict[str, Streamer] = {}
persistent_capturer: Optional[PersistentCapturer] = None
stop_event = threading.Event()

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure refs directories exist
    if app_config and app_config.detector.enabled:
        for state_name, state_cfg in app_config.detector.states.items():
            refs_dir = Path(state_cfg.refs_dir)
            if not refs_dir.exists():
                try:
                    refs_dir.mkdir(parents=True, exist_ok=True)
                    print(f"[Startup] Created refs dir: {refs_dir}")
                except Exception as e:
                    print(f"[Startup] Failed to create refs dir {refs_dir}: {e}")
    
    # Start detector loop
    if detector_instance and detector_instance.config.detector.enabled:
        detect_thread = threading.Thread(target=detection_loop, daemon=True)
        detect_thread.start()
    
    # Snapshot loop startup (Independent of detector instance)
    if app_config and app_config.snapshot.enabled:
        snap_thread = threading.Thread(target=snapshot_loop, daemon=True)
        snap_thread.start()

    yield
    # Shutdown
    stop_event.set()
    if detector_instance and 'detect_thread' in locals():
        detect_thread.join(timeout=2.0)
    if 'snap_thread' in locals():
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
# Loops
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

def snapshot_loop():
    """
    持久化截圖迴圈：使用單一 FFmpeg 進程持續截圖
    只建立一次 SRS 連線，大幅減少 on_play/on_stop 事件
    """
    global app_config, persistent_capturer
    if not app_config: return

    snap_cfg = app_config.snapshot
    print(f"[Snapshot] Starting persistent snapshot service. Target: {snap_cfg.target_stream}, Interval: {snap_cfg.interval}s")
    
    # Resolve capture URL
    url = snap_cfg.url
    if not url:
        if snap_cfg.target_stream in app_config.streams:
            stream_conf = app_config.streams[snap_cfg.target_stream]
            url = stream_conf.rtmp_url
    
    if not url:
        print("[Snapshot] Error: No URL found for snapshot service")
        return

    # Create persistent capturer (single long-running FFmpeg process)
    sc = CapturerStreamConfig(url=url, scale="640:-1", quality=snap_cfg.quality) 
    persistent_capturer = PersistentCapturer(sc, snap_cfg.output_path, snap_cfg.interval)
    
    # Start the persistent FFmpeg process
    if not persistent_capturer.start():
        print("[Snapshot] Failed to start persistent capturer")
        return
    
    # Monitor loop: just ensure the process is running, auto-restart if needed
    while not stop_event.is_set():
        persistent_capturer.ensure_running()
        # Check every 5 seconds (not every capture interval)
        stop_event.wait(5.0)
    
    # Cleanup on shutdown
    print("[Snapshot] Stopping persistent capturer...")
    persistent_capturer.stop()
    persistent_capturer = None

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

@app.get("/api/config/states")
def get_configured_states():
    """Get list of all configured state names"""
    if not detector_instance:
         return {"states": []}
    return {"states": list(detector_instance.config.detector.states.keys())}

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

#Helper to get config independent of detector state
def get_detector_config():
    if detector_instance:
        return detector_instance.config.detector
    if app_config:
        return app_config.detector
    raise HTTPException(500, "Configuration not loaded")

@app.get("/api/refs/{state}")
def list_refs(state: str):
    """List reference images for a given state"""
    det_cfg = get_detector_config()
         
    if state not in det_cfg.states:
        raise HTTPException(404, "State not found")
        
    ref_dir = Path(det_cfg.states[state].refs_dir)
    if not ref_dir.exists():
        return []
        
    # Return list of files relative to ref_dir or just names
    files = sorted([f.name for f in ref_dir.glob("*.jpg")])
    return files

@app.post("/api/refs/{state}")
def add_ref(state: str):
    """Capture current frame and save as reference for state"""
    try:
        det_cfg = get_detector_config()
        
        if state not in det_cfg.states:
            raise HTTPException(404, "State not found")
            
        # Capture logic optimized: STRICTLY use existing snapshot
        if not app_config or not app_config.snapshot.enabled:
            raise HTTPException(400, "Reference capture failed: Snapshot service is disabled. Please enable 'snapshot' in config.")

        snap_path = Path(app_config.snapshot.output_path)
        if not snap_path.exists():
            raise HTTPException(400, "Reference capture failed: Snapshot file not found. Service might be starting or failed.")
            
        tmp_path = str(snap_path)
        print(f"[API] Using background snapshot: {tmp_path}")

        ref_dir = Path(det_cfg.states[state].refs_dir)
        ref_dir.mkdir(parents=True, exist_ok=True)
        
        ts = int(time.time())
        filename = f"ref_{ts}.jpg"
        dst = ref_dir / filename
        
        shutil.copy(tmp_path, dst)
        
        # Reload detector refs if running
        if detector_instance:
            detector_instance.ref_mgr.load_all()
        
        return {"status": "saved", "filename": filename}

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Critical error in add_ref: {e}")

@app.get("/api/refs/{state}/{filename}/image")
def get_ref_image(state: str, filename: str):
    det_cfg = get_detector_config()
         
    if state not in det_cfg.states:
        raise HTTPException(404, "State not found")
        
    ref_dir = Path(det_cfg.states[state].refs_dir)
    path = ref_dir / filename
    
    if path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
    else:
        raise HTTPException(404, "File not found")

@app.delete("/api/refs/{state}/{filename}")
def delete_ref(state: str, filename: str):
    det_cfg = get_detector_config()
         
    if state not in det_cfg.states:
        raise HTTPException(404, "State not found")
        
    ref_dir = Path(det_cfg.states[state].refs_dir)
    path = ref_dir / filename
    
    if path.exists():
        path.unlink()
        # Reload detector refs if running
        if detector_instance:
             detector_instance.ref_mgr.load_all()
        return {"status": "deleted"}
    else:
        raise HTTPException(404, "File not found")

@app.get("/api/live/frame")
def get_live_frame():
    """Get the latest snapshot frame for preview (from snapshot service)"""
    if not app_config or not app_config.snapshot.enabled:
        raise HTTPException(404, "Snapshot service disabled")
    
    path = Path(app_config.snapshot.output_path)
    if not path.exists():
        raise HTTPException(404, "Snapshot not yet available")
    
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

# --- Snapshot / Preview ---

class SnapshotReq(BaseModel):
    output_dir: str
    count: int = 1
    interval_ms: int = 0
    prefix: str = "snap"

@app.post("/snapshot/save")
def save_snapshot(req: SnapshotReq):
    """Save current snapshot(s) to specified directory"""
    if not app_config or not app_config.snapshot.enabled:
        raise HTTPException(400, "Snapshot service is disabled")
    
    snap_path = Path(app_config.snapshot.output_path)
    if not snap_path.exists():
        raise HTTPException(404, "Snapshot not yet available")
    
    out = Path(req.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    
    saved = []
    try:
        for i in range(req.count):
            ts = int(time.time() * 1000)
            dst = out / f"{req.prefix}_{ts}_{i}.jpg"
            shutil.copy(snap_path, dst)
            saved.append(str(dst))
            
            if i < req.count - 1 and req.interval_ms > 0:
                time.sleep(req.interval_ms / 1000.0)
                
    except Exception as e:
        raise HTTPException(500, f"Snapshot save failed: {e}")
        
    return {"saved": len(saved), "files": saved}


@app.get("/api/snapshot/latest")
def get_latest_snapshot():
    """Get the latest image from the background snapshot service"""
    if not app_config or not app_config.snapshot.enabled:
        raise HTTPException(404, "Snapshot service disabled")
        
    path = Path(app_config.snapshot.output_path)
    if not path.exists():
        raise HTTPException(404, "Snapshot not yet available")
        
    from fastapi.responses import FileResponse
    # Disable cache to ensure live update
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


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
    global detector_instance, streamers, app_config
    
    app_config = config
    
    if config.detector.enabled:
        detector_instance = EgmStateDetector(config)
    
    streamers = stream_instances
    return app
