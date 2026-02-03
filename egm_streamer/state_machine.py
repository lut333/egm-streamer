from typing import Dict
from .models import DebounceConfig

class StateMachine:
    def __init__(self, config: DebounceConfig):
        self.config = config
        self.current_state = "OTHER"
        
        # Counters
        self.streaks: Dict[str, int] = {}  # 連續命中計數
        self.none_streak = 0               # 連續不命中計數

    def update(self, detected_state: str) -> str:
        """
        輸入當前 frame 的瞬時偵測結果 (NORMAL, SELECT, PLAYING, or OTHER)
        回傳經過穩定化後的狀態
        """
        
        # 1. Update streaks
        if detected_state != "OTHER":
            self.streaks[detected_state] = self.streaks.get(detected_state, 0) + 1
            self.none_streak = 0
            
            # Reset other streaks ?? 
            # 策略：如果同時有多個狀態命中（雖然 Detector 通常會選一個優先），
            # 這裡只處理單一輸入。所以把其他非 detected_state 的計數歸零比較安全，
            # 避免跳來跳去累積
            for s in list(self.streaks.keys()):
                if s != detected_state:
                    self.streaks[s] = 0
        else:
            self.none_streak += 1
            # 沒命中時，所有命中 streak 歸零
            self.streaks.clear()

        # 2. State Transition Logic
        
        # case A: 當前是 OTHER，想要切換到某個有效狀態
        if self.current_state == "OTHER":
            if detected_state != "OTHER":
                if self.streaks[detected_state] >= self.config.confirm_frames:
                    self.current_state = detected_state
        
        # case B: 當前是某個有效狀態 (e.g. NORMAL)
        else:
            if detected_state == self.current_state:
                # 持續命中，狀態維持
                pass
            elif detected_state != "OTHER":
                # 偵測到另一個有效狀態 (e.g. NORMAL -> SELECT)
                # 必須等新狀態累積足夠
                if self.streaks[detected_state] >= self.config.confirm_frames:
                    self.current_state = detected_state
            else:
                # 瞬間變成 OTHER (掉偵測)
                # 必須等 drop_frames 夠久才真正切回 OTHER
                if self.none_streak >= self.config.drop_frames:
                    self.current_state = "OTHER"

        return self.current_state
