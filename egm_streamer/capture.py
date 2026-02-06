import os
import time
import subprocess
import threading
from pathlib import Path
from typing import Optional
from PIL import Image
from .models import StreamConfig

class CaptureError(Exception):
    pass

class StreamCapturer:
    """單次截圖器 (舊版，每次呼叫建立新連線)"""
    def __init__(self, config: StreamConfig, output_path: str = "/dev/shm/latest.jpg"):
        self.config = config
        self.output_path = Path(output_path)
        self.tmp_path = self.output_path.with_suffix(".tmp.jpg")
        self.last_capture_time = 0.0

    def capture(self) -> str:
        """
        同步執行一次 FFmpeg 截圖
        Return: 截圖檔案路徑
        """
        cmd = [
            "/usr/bin/ffmpeg",
            "-hide_banner", "-loglevel", "error", "-y",
            "-rw_timeout", str(self.config.rw_timeout_us),
            "-i", self.config.url,
            "-an", "-sn", "-dn",
            "-frames:v", "1",
            "-vf", f"scale={self.config.scale}",
            "-q:v", str(self.config.quality),
            "-f", "image2", "-vcodec", "mjpeg",
            str(self.tmp_path)
        ]

        try:
            start = time.time()
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            os.replace(self.tmp_path, self.output_path)
            self.last_capture_time = time.time()
            return str(self.output_path)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if e.stderr else "unknown error"
            raise CaptureError(f"FFmpeg failed: {err}")
        except Exception as e:
            raise CaptureError(f"Capture failed: {e}")

    def get_image(self) -> Image.Image:
        """讀取最新的截圖並轉為 PIL Image"""
        path = self.capture()
        try:
            with open(path, "rb") as f:
                img = Image.open(f)
                img.load()
                return img.convert("L")
        except Exception as e:
            raise CaptureError(f"Image load failed: {e}")


class PersistentCapturer:
    """
    持久化截圖器：使用單一 FFmpeg 進程持續截圖
    
    優點：只建立一次串流連線，減少 SRS on_play/on_stop 事件
    使用 FFmpeg -update 1 參數持續覆蓋同一檔案
    """
    def __init__(self, config: StreamConfig, output_path: str, interval: float = 1.0):
        self.config = config
        self.output_path = Path(output_path)
        self.interval = max(0.1, interval)  # 最小 0.1 秒
        self.process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._stop_requested = False
        self._restart_count = 0
        self._last_start_time = 0.0
        
    def _build_cmd(self) -> list:
        """建構 FFmpeg 命令"""
        # fps filter: 1/interval = frames per second
        # 例如 interval=1.0 -> fps=1, interval=0.5 -> fps=2
        fps_value = 1.0 / self.interval
        
        return [
            "/usr/bin/ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-rw_timeout", str(self.config.rw_timeout_us),
            "-i", self.config.url,
            "-an", "-sn", "-dn",
            "-vf", f"fps={fps_value},scale={self.config.scale}",
            "-q:v", str(self.config.quality),
            "-f", "image2",
            "-update", "1",  # 關鍵：持續覆蓋同一檔案
            "-y",
            str(self.output_path)
        ]
        
    def start(self) -> bool:
        """啟動持久化 FFmpeg 進程"""
        with self._lock:
            if self.process and self.process.poll() is None:
                print("[PersistentCapturer] Already running")
                return True
                
            self._stop_requested = False
            cmd = self._build_cmd()
            
            try:
                print(f"[PersistentCapturer] Starting: {' '.join(cmd)}")
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True
                )
                self._last_start_time = time.time()
                print(f"[PersistentCapturer] Started with PID {self.process.pid}")
                return True
            except Exception as e:
                print(f"[PersistentCapturer] Failed to start: {e}")
                return False
    
    def stop(self):
        """停止 FFmpeg 進程"""
        with self._lock:
            self._stop_requested = True
            if self.process:
                print(f"[PersistentCapturer] Stopping PID {self.process.pid}")
                try:
                    self.process.terminate()
                    self.process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    print("[PersistentCapturer] Force killing...")
                    self.process.kill()
                    self.process.wait()
                except Exception as e:
                    print(f"[PersistentCapturer] Stop error: {e}")
                finally:
                    self.process = None
                    
    def is_running(self) -> bool:
        """檢查進程是否運行中"""
        with self._lock:
            return self.process is not None and self.process.poll() is None
    
    def ensure_running(self) -> bool:
        """確保進程運行，若停止則重啟"""
        if self._stop_requested:
            return False
            
        if self.is_running():
            return True
            
        # 進程已停止，檢查錯誤訊息
        with self._lock:
            if self.process:
                stderr_output = ""
                try:
                    stderr_output = self.process.stderr.read() if self.process.stderr else ""
                except:
                    pass
                    
                exit_code = self.process.returncode
                print(f"[PersistentCapturer] Process exited with code {exit_code}")
                if stderr_output:
                    print(f"[PersistentCapturer] Stderr: {stderr_output[:500]}")
                self.process = None
        
        # 避免快速重啟循環
        time_since_last = time.time() - self._last_start_time
        if time_since_last < 5.0:
            wait_time = 5.0 - time_since_last
            print(f"[PersistentCapturer] Waiting {wait_time:.1f}s before restart...")
            time.sleep(wait_time)
        
        self._restart_count += 1
        print(f"[PersistentCapturer] Restarting (attempt #{self._restart_count})")
        return self.start()
    
    @property
    def stats(self) -> dict:
        """取得運行狀態"""
        return {
            "running": self.is_running(),
            "pid": self.process.pid if self.process else None,
            "restart_count": self._restart_count,
            "uptime": time.time() - self._last_start_time if self._last_start_time else 0
        }
