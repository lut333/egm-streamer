import os
import json
import glob
from pathlib import Path
from typing import List, Dict, Optional
from PIL import Image
import imagehash

from .models import StateConfig, ROI, DetectionConfig
from .hasher import compute_hash, hex_to_hash

class ReferenceManager:
    """管理參考圖片的 Hash Cache，避免每次重新讀圖計算"""
    
    def __init__(self, states: Dict[str, StateConfig], detection_config: DetectionConfig):
        self.states = states
        self.cfg = detection_config
        # Cache structure: { state_name: { roi_name: [ImageHash, ...] } }
        self.caches: Dict[str, Dict[str, List[imagehash.ImageHash]]] = {}
        self.mtimes: Dict[str, float] = {}  # 用來偵測目錄更新

    def load_all(self):
        """載入所有狀態的參考圖片"""
        print(f"[RefMgr] Loading references for states: {list(self.states.keys())}")
        for name, config in self.states.items():
            self._load_state_refs(name, config)

    def _load_state_refs(self, state_name: str, config: StateConfig):
        ref_dir = Path(config.refs_dir)
        if not ref_dir.exists():
            print(f"[WARN] Refs dir not found: {ref_dir}")
            self.caches[state_name] = {}
            return

        # 檢查 mtime 是否變更
        current_mtime = self._get_dir_mtime(ref_dir)
        if self.mtimes.get(state_name) == current_mtime:
            return  # 無變更，跳過

        print(f"[RefMgr] (Re)loading refs for {state_name} from {ref_dir}")
        
        # 載入所有圖片
        files = sorted(glob.glob(str(ref_dir / "*")))
        images = []
        for f in files:
            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                try:
                    img = Image.open(f).convert("L")
                    images.append(img)
                except Exception as e:
                    print(f"[WARN] Bad image {f}: {e}")

        # 對每個 ROI 計算 Hash
        state_cache = {}
        for roi in config.rois:
            hashes = []
            for img in images:
                cropped = img.crop((roi.x, roi.y, roi.x + roi.w, roi.y + roi.h))
                h = compute_hash(cropped, self.cfg.algo, self.cfg.hash_size)
                hashes.append(h)
            state_cache[roi.name] = hashes
        
        self.caches[state_name] = state_cache
        self.mtimes[state_name] = current_mtime
        print(f"  Loaded {len(images)} refs, {len(config.rois)} ROIs")

    def _get_dir_mtime(self, p: Path) -> float:
        try:
            return max(f.stat().st_mtime for f in p.glob("*") if f.is_file())
        except ValueError:
            return 0.0

    def get_hashes(self, state_name: str, roi_name: str) -> List[imagehash.ImageHash]:
        return self.caches.get(state_name, {}).get(roi_name, [])

    def reload_if_needed(self):
        """定期呼叫此方法檢查是否有新的參考圖"""
        for name, config in self.states.items():
            current_mtime = self._get_dir_mtime(Path(config.refs_dir))
            if current_mtime > self.mtimes.get(name, 0):
                self._load_state_refs(name, config)
