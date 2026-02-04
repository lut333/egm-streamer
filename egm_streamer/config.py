import yaml
from pathlib import Path
from .models import AppConfig

def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    # 轉換 states list to dict (方便 YAML 書寫)
    # 假設 YAML 裡的 states 還是 list of dict 比較好寫，這裡轉一下
    # 或是直接讓 YAML key 就是 state name
    
    # Post-process: Resolve linked ROIs (missing coords)
    app_config = AppConfig(**data)
    
    detector_cfg = app_config.detector
    states = detector_cfg.states
    
    for state_name, state_config in states.items():
        for i, roi in enumerate(state_config.rois):
            # Check if this is a linked ROI (has ref_state but missing coords)
            if roi.ref_state and (roi.x is None or roi.y is None or roi.w is None or roi.h is None):
                target_state_name = roi.ref_state
                
                # Check if target state exists
                if target_state_name not in states:
                    raise ValueError(f"State '{state_name}' ROI '{roi.name}' references unknown state '{target_state_name}'")
                
                target_state = states[target_state_name]
                
                # Find matching ROI in target state
                target_roi = next((r for r in target_state.rois if r.name == roi.name), None)
                
                if not target_roi:
                     raise ValueError(f"State '{state_name}' ROI '{roi.name}' references ROI '{roi.name}' in '{target_state_name}', but it does not exist")
                
                # Verify target has coordinates (recursive chain check ideally, but simple for now)
                if target_roi.x is None:
                     raise ValueError(f"Target ROI '{roi.name}' in '{target_state_name}' also has missing coordinates (chained linking not fully supported yet)")

                # Copy coordinates
                roi.x = target_roi.x
                roi.y = target_roi.y
                roi.w = target_roi.w
                roi.h = target_roi.h
                
                # Note: We modified the ROI object in place within the list

    return app_config
