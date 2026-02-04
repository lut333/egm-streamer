import io
import time
import json
import os
from pathlib import Path
from typing import Optional, Dict

from PIL import Image

from .models import AppConfig, DetectionResult, MatchResult
from .refs import ReferenceManager
from .matcher import Matcher
from .state_machine import StateMachine
from .notifier import TelegramNotifier


class EgmStateDetector:
    """
    State detector that reads from snapshot service's output file.
    
    IMPORTANT: This detector does NOT capture screenshots itself.
    It requires the snapshot service to be enabled and running.
    The snapshot service writes to config.snapshot.output_path,
    and the detector reads from that file for state matching.
    """
    
    def __init__(self, config: AppConfig):
        self.config = config
        det_cfg = config.detector
        
        # Use snapshot service's output path for reading images
        self.snapshot_path = Path(config.snapshot.output_path)
        if not config.snapshot.enabled:
            print("[Detector] WARNING: Snapshot service is disabled! Detector requires it to function.")
        
        self.ref_mgr = ReferenceManager(det_cfg.states, det_cfg.detection)
        self.matcher = Matcher(self.ref_mgr, det_cfg.detection.algo, det_cfg.detection.hash_size)
        self.sm = StateMachine(det_cfg.debounce)
        
        # Notifier
        self.notifier = TelegramNotifier(det_cfg.telegram, config.common.instance_id)
        self._last_state = "UNKNOWN"
        
        # Initial load
        self.ref_mgr.load_all()
    
    def _read_snapshot_image(self) -> Image.Image:
        """
        Read the latest snapshot image produced by snapshot service.
        Uses atomic read to avoid reading a partial image during write.
        """
        if not self.snapshot_path.exists():
            raise FileNotFoundError(f"Snapshot file not found: {self.snapshot_path}")
        
        # Read entire file into memory first (atomic read pattern)
        with open(self.snapshot_path, "rb") as f:
            data = f.read()
        
        img = Image.open(io.BytesIO(data))
        img.load()  # Force decode to catch truncated images
        return img.convert("L")  # Convert to grayscale

    def step(self) -> DetectionResult:
        """執行一次完整的偵測流程"""
        
        # 0. Check refs update
        self.ref_mgr.reload_if_needed()

        # 1. Read snapshot image (produced by snapshot service)
        matches_summary: Dict[str, MatchResult] = {}
        
        try:
            img = self._read_snapshot_image()
        except Exception as e:
            # Snapshot read failed (file not found, corrupt image, etc.)
            print(f"[Detector] Snapshot read failed: {e}")
            return DetectionResult(
                state="UNKNOWN",
                matches={},
                timestamp=time.time()
            )

        # 2. Match all states
        best_candidate = "OTHER"
        det_cfg = self.config.detector
        
        # 依優先順序比對
        for state_name in det_cfg.priority:
            state_cfg = det_cfg.states.get(state_name)
            if not state_cfg: 
                continue
                
            is_match, matched_rois, avg_dist = self.matcher.match_state(
                img, state_name, state_cfg.rois, state_cfg.match_policy
            )
            
            matches_summary[state_name] = MatchResult(
                state=state_name,
                matched_rois=matched_rois,
                avg_distance=avg_dist,
                is_match=is_match
            )
            
            # 如果還沒找到最佳候選且這個中了，就設為候選
            # 因為是按優先順序跑的，所以第一個中的就是最佳候選
            if best_candidate == "OTHER" and is_match:
                best_candidate = state_name

        # 3. State Machine Update
        final_state = self.sm.update(best_candidate)
        
        result = DetectionResult(
            state=final_state,
            matches=matches_summary,
            timestamp=time.time()
        )
        
        # 4. Notify on state change (isolated - errors won't affect detection)
        if final_state != self._last_state:
            print(f"[Detector] State changed: {self._last_state} -> {final_state}")
            try:
                self.notifier.send_state_change(self._last_state, final_state)
            except Exception as e:
                print(f"[Detector] Notifier error (ignored): {e}")
            self._last_state = final_state
        
        # 5. Output to file
        self._write_status(result)
        
        return result

    def _write_status(self, res: DetectionResult):
        path = self.config.detector.output.status_file
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(res.model_dump_json())
            os.replace(tmp, path)
        except Exception as e:
            print(f"[Error] Write status failed: {e}")

    def run_forever(self):
        interval = self.config.detector.capture.capture_interval
        print(f"Starting detection loop. Interval={interval}s")
        while True:
            t0 = time.time()
            res = self.step()
            
            # Log change (optional)
            # print(f"State: {res.state}")
            
            dt = time.time() - t0
            sleep_time = max(0, interval - dt)
            time.sleep(sleep_time)
