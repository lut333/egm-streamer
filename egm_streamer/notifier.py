import threading
import requests
import time
from .models import TelegramConfig

class TelegramNotifier:
    def __init__(self, config: TelegramConfig, instance_id: str):
        self.config = config
        self.instance_id = config.client_id if config.client_id else instance_id
        self._running = True
        
    def send_state_change(self, prev_state: str, new_state: str):
        if not self.config.enabled or not self.config.bot_token or not self.config.chat_id:
            return
            
        # Check specific notification states
        if self.config.notify_states:
            if new_state not in self.config.notify_states:
                return
        
        # State descriptions
        state_info = {
            "PLAYING": "🎰 Free Game 遊戲中",
            "SELECT": "🎯 Free Game 選擇畫面",
            "NORMAL": "📺 一般遊戲畫面",
            "OTHER": "❓ 未知狀態",
            "UNKNOWN": "⚠️ 偵測中斷",
        }
        
        desc = state_info.get(new_state, f"❓ {new_state}")
        
        msg = (
            f"<b>{desc}</b>\n"
            f"<code>{self.instance_id}</code> | {time.strftime('%H:%M:%S')}"
        )
        
        # Fire and forget thread
        t = threading.Thread(target=self._send, args=(msg,))
        t.start()
        
    def _send(self, text: str):
        try:
            url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
            payload = {
                "chat_id": self.config.chat_id,
                "text": text,
                "parse_mode": "HTML"
            }
            resp = requests.post(url, json=payload, timeout=5)
            if not resp.ok:
                print(f"[Notifier] TG Send failed: {resp.text}")
        except Exception as e:
            print(f"[Notifier] Error: {e}")
