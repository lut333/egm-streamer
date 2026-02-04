import os
import time
import subprocess
from pathlib import Path
from PIL import Image
from .models import StreamConfig

class CaptureError(Exception):
    pass

class StreamCapturer:
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
            "-q:v", str(self.config.quality), # Use configured quality
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
                return img.convert("L")  # 轉灰階
        except Exception as e:
            raise CaptureError(f"Image load failed: {e}")
