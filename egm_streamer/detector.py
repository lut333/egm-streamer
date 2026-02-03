import time
import json
import os
from typing import Optional, Dict

from .models import AppConfig, DetectionResult, MatchResult
from .capture import StreamCapturer
from .refs import ReferenceManager
from .matcher import Matcher
from .state_machine import StateMachine

class EgmStateDetector:
    def __init__(self, config: AppConfig):
        self.config = config
        det_cfg = config.detector
        
        # Resolve capture URL from target stream if specified
        if det_cfg.target_stream and det_cfg.target_stream in config.streams:
            stream_cfg = config.streams[det_cfg.target_stream]
            # Override capture URL with the stream's output URL
            # Note: This assumes detector can read from the RTMP output of the streamer
            if not det_cfg.capture.url:
                det_cfg.capture.url = stream_cfg.rtmp_url
        
        self.capturer = StreamCapturer(det_cfg.capture)
        self.ref_mgr = ReferenceManager(det_cfg.states, det_cfg.detection)
        self.matcher = Matcher(self.ref_mgr, det_cfg.detection.algo, det_cfg.detection.hash_size)
        self.sm = StateMachine(det_cfg.debounce)
        
        # Initial load
        self.ref_mgr.load_all()

    def step(self) -> DetectionResult:
        """執行一次完整的偵測流程"""
        
        # 0. Check refs update
        self.ref_mgr.reload_if_needed()

        # 1. Capture & Average
        # 依照 config.detection.samples 進行採樣 (如果需要多張平均)
        # 這裡簡化：只抓一張，或者在 detector 內做多張 loop
        # 用戶需求有提到 samples, 這裡簡單實作
        
        matches_summary: Dict[str, MatchResult] = {}
        
        # 簡單策略：只抓一張做代表，如果需要抗噪，應該在 capture 層或這裡做多張投票
        # 根據 freegame_classify.py 的邏輯，它是抓 samples 張取 best-k mean
        # 這裡我們採用：抓一張，依靠 StateMachine 去濾除雜訊 (debounce)
        # 如果用戶需要單幀抗噪，可以在這裡 loop
        
        try:
            img = self.capturer.get_image()
        except Exception as e:
            # Capture failed
            print(f"[Detector] Capture failed: {e}")
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
        
        # 4. Output to file
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
