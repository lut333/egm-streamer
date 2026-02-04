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
        roi_count = 0  # Count of ROIs with refs (for avg calculation)
        
        required_missed = False

        for roi in rois:
            # Crop
            cropped = img.crop((roi.x, roi.y, roi.x + roi.w, roi.y + roi.h))
            current_hash = compute_hash(cropped, self.algo, self.hash_size)
            
            # Get refs (Use specific state if defined, else current state)
            target_ref_state = roi.ref_state if roi.ref_state else state_name
            refs = self.ref_mgr.get_hashes(target_ref_state, roi.name)
            
            if not refs:
                # No refs - can't calculate distance
                if roi.required:
                    required_missed = True
                continue

            # Find min distance
            min_dist = float('inf')
            for r in refs:
                dist = current_hash - r
                if dist < min_dist:
                    min_dist = dist
            
            # Debug negative ROI
            if roi.negative:
                print(f"[Matcher] State:{state_name} ROI:{roi.name} IsNegative:True TargetRef:{target_ref_state} MinDist:{min_dist} Thresh:{policy.threshold}")

            # Track all distances for averaging
            contribution = min_dist
            
            if roi.negative:
                if min_dist <= policy.threshold:
                    # Negative ROI FOUND (Bad) -> Apply penalty
                    contribution = 100.0 
                    if roi.required:
                        required_missed = True # Required to be ABSENT, but was found
                else:
                    # Negative ROI NOT FOUND (Good) -> No penalty
                    contribution = 0.0
            else:
                # Normal ROI
                if min_dist <= policy.threshold:
                    matched_rois.append(roi.name)
                    match_count += 1
                elif roi.required:
                    required_missed = True
            
            total_dist += contribution
            roi_count += 1
            
            # if min_dist <= policy.threshold: ... (Removed old logic)

        if required_missed:
            # Return actual avg_dist even on required miss (for debugging)
            avg_dist = (total_dist / roi_count) if roi_count > 0 else -1.0
            return False, matched_rois, avg_dist if avg_dist >= 0 else 999.0

        avg_dist = (total_dist / roi_count) if roi_count > 0 else -1.0
        
        # CORRECT LOGIC START
        # A match is valid ONLY if the overall average distance is within the threshold.
        # This allows negative ROIs (which add +100 distance) to effectively fail the match.
        is_match = (match_count >= policy.min_match) and (avg_dist <= policy.threshold)
        # CORRECT LOGIC END
        
        # Additional check: max_match (if set, though usually min_match is the concern)
        if policy.max_match > 0 and match_count > policy.max_match:
            # 這裡看需求，通常符合越多越好，不需要因為符合太多而視為 false
            pass 

        return is_match, matched_rois, avg_dist
