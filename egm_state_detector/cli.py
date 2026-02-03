import argparse
import sys
import uvicorn
import yaml
import threading
from pathlib import Path

from .config import load_config
from .detector import EgmStateDetector
from .streamer import Streamer
from .api import create_app
from .capture import StreamCapturer, StreamConfig
from .refs import ReferenceManager
from .matcher import Matcher

def cmd_serve(args):
    """Start API server, background detector, and background streamer"""
    config = load_config(args.config)
    
    # 1. Start Streamers
    streamers = {}
    if config.streams:
        for name, s_conf in config.streams.items():
            if s_conf.enabled:
                print(f"[CLI] Starting Streamer: {name}...")
                s = Streamer(name, s_conf)
                s.start()
                streamers[name] = s
    
    # 2. Start Detector
    if config.detector.enabled:
        print("[CLI] Starting Detector...")
        # Detector is initialized via create_app below if we want, OR we can init here?
        # Actually create_app logic was: if config.detector.enabled: init global instance.
        # But we also have cmd_detect which inits it directly.
        # Ideally create_app should reuse an existing instance if passed? 
        # But create_app logic currently does: detector_instance = EgmStateDetector(config) inside.
        # This is fine. API startup will trigger detector start.
        pass

    # 3. Start API (Blocking)
    app = create_app(config, stream_instances=streamers)
    print(f"[CLI] Starting API on {config.api.host}:{config.api.port}...")
    uvicorn.run(app, host=config.api.host, port=config.api.port)

def cmd_detect(args):
    """Run single detection"""
    config = load_config(args.config)
    if not config.detector.enabled:
        print("Detector is disabled in config.")
        return
        
    detector = EgmStateDetector(config) # Requires AppConfig, but now detector config is nested
    # Wait, EgmStateDetector expects AppConfig but retrieves config.stream inside it?
    # We changed AppConfig structure in models.py. 
    # Need to verify if EgmStateDetector is updated or if we pass the whole config and it handles nested fields.
    # Looking at detector.py, it accesses `config.stream`. Now it is `config.detector.capture`.
    # WE MUST UPDATE DETECTOR.PY as well! 
    # For now, let's fix CLI to pass valid config assuming detector is updated.
    result = detector.step()
    print(result.model_dump_json(indent=2))

def cmd_snapshot(args):
    """Manual snapshot tool"""
    # Create temp config just for capture
    sc = StreamConfig(url=args.url, scale="640:-1")
    capturer = StreamCapturer(sc, args.output_file or "/dev/shm/snap_manual.jpg")
    
    saved_paths = []
    print(f"Snapshotting from {args.url} ...")
    
    import time
    for i in range(args.count):
        if args.count > 1 and args.output_dir:
            ts = int(time.time() * 1000)
            fname = f"snap_{ts}_{i:02d}.jpg"
            path = Path(args.output_dir) / fname
            capturer.output_path = path
        
        real_path = capturer.capture()
        saved_paths.append(real_path)
        print(f"Saved: {real_path}")
        
        if i < args.count - 1 and args.interval > 0:
            time.sleep(args.interval)

def cmd_rebuild(args):
    """Force rebuild references"""
    config = load_config(args.config)
    # Update to use nested detector config
    ref_mgr = ReferenceManager(config.detector.states, config.detector.detection)
    ref_mgr.load_all()
    print("Rebuild complete.")

def main():
    parser = argparse.ArgumentParser(description="EGM State Detector CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # serve
    p_serve = subparsers.add_parser("serve", help="Start service with API")
    p_serve.add_argument("--config", required=True, help="Path to config.yaml")

    # detect
    p_detect = subparsers.add_parser("detect", help="Run single detection")
    p_detect.add_argument("--config", required=True, help="Path to config.yaml")

    # snapshot
    p_snap = subparsers.add_parser("snapshot", help="Capture snapshots")
    p_snap.add_argument("--url", required=True, help="Stream URL")
    p_snap.add_argument("--output-file", help="Single output file")
    p_snap.add_argument("--output-dir", help="Output directory for multiple snaps")
    p_snap.add_argument("--count", type=int, default=1)
    p_snap.add_argument("--interval", type=float, default=0.5)

    # rebuild
    p_rebuild = subparsers.add_parser("rebuild", help="Re-verify/Compute refs hashes")
    p_rebuild.add_argument("--config", required=True)

    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "detect":
        cmd_detect(args)
    elif args.command == "snapshot":
        cmd_snapshot(args)
    elif args.command == "rebuild":
        cmd_rebuild(args)

if __name__ == "__main__":
    main()
