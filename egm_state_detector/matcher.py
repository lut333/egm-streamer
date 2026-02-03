from PIL import Image
from typing import List, Tuple
import imagehash
from .models import ROI, MatchPolicy
from .refs import ReferenceManager
from .hasher import compute_hash

class Matcher:
    def __init__(self, ref_mgr: ReferenceManager, algo: str = "dhash", hash_size: int = 8):
        self.ref_mgr = ref_mgr
        self.algo = algo
        self.hash_size = hash_size

    def match_state(self, img: Image.Image, state_name: str, rois: List[ROI], policy: MatchPolicy) -> Tuple[bool, List[str], float]:
        """
        比對單一狀態
        Return: (is_match, matched_roi_names, avg_distance)
        """
        matched_rois = []
        total_dist = 0.0
        match_count = 0
        
        required_missed = False

        for roi in rois:
            # Crop
            cropped = img.crop((roi.x, roi.y, roi.x + roi.w, roi.y + roi.h))
            current_hash = compute_hash(cropped, self.algo, self.hash_size)
            
            # Get refs
            refs = self.ref_mgr.get_hashes(state_name, roi.name)
            if not refs:
                # No refs implies no match possible for this ROI
                if roi.required:
                    required_missed = True
                continue

            # Find min distance
            min_dist = 999
            for r in refs:
                dist = current_hash - r
                if dist < min_dist:
                    min_dist = dist
            
            if min_dist <= policy.threshold:
                matched_rois.append(roi.name)
                total_dist += min_dist
                match_count += 1
            elif roi.required:
                required_missed = True

        if required_missed:
            return False, matched_rois, 999.0

        avg_dist = (total_dist / match_count) if match_count > 0 else 999.0
        
        is_match = (match_count >= policy.min_match)
        
        # Additional check: max_match (if set, though usually min_match is the concern)
        if policy.max_match > 0 and match_count > policy.max_match:
            # 這裡看需求，通常符合越多越好，不需要因為符合太多而視為 false
            pass 

        return is_match, matched_rois, avg_dist
