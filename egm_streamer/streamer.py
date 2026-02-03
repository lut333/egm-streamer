import os
import time
import subprocess
import threading
import signal
import re
from pathlib import Path
from typing import Optional, List

from .models import StreamerConfig, StreamStatus

class Streamer:
    def __init__(self, name: str, config: StreamerConfig):
        self.name = name
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.stop_event = threading.Event()
        self.status = StreamStatus(
            name=name,
            running=False, pid=None, fps=0.0, bitrate="0", uptime=0.0, last_update=0.0
        )
        self.start_time = 0.0

    def start(self):
        """Start the streamer in a background thread"""
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the streamer"""
        self.stop_event.set()
        if self.process:
            try:
                os.kill(self.process.pid, signal.SIGTERM)
                # self.process.wait(timeout=3.0) 
            except Exception:
                pass
        if hasattr(self, 'thread'):
            self.thread.join(timeout=2.0)

    def _run_loop(self):
        while not self.stop_event.is_set():
            try:
                self._run_ffmpeg()
            except Exception as e:
                print(f"[Streamer:{self.name}] FFmpeg crashed or failed: {e}")
            
            if not self.stop_event.is_set():
                print(f"[Streamer:{self.name}] Restarting in 3 seconds...")
                time.sleep(3)

    def _run_ffmpeg(self):
        if not self.config.rtmp_url:
            print(f"[Streamer:{self.name}] Error: RTMP URL not configured")
            time.sleep(5) # Prevent tight loop
            return

        # Strict low-latency parameters from user request
        p = self.config.ffmpeg_params
        
        cmd = [
            "/usr/bin/ffmpeg",
            "-hide_banner", "-nostats", "-loglevel", "warning",
            "-probesize", "32", "-analyzeduration", "0", # Ultra low latency input
            "-progress", "pipe:1",
            "-flags", "low_delay", 
            "-fflags", "+genpts+nobuffer", "-use_wallclock_as_timestamps", "1", # Added +nobuffer
            "-f", "v4l2", "-thread_queue_size", "1024", 
            "-video_size", self.config.resolution, 
            "-i", self.config.input_device
        ]

        if self.config.audio_device:
            cmd += [
                "-f", "alsa", "-thread_queue_size", "4096", 
                "-i", self.config.audio_device,
                "-c:a", "aac", "-b:a", "96k",
                "-af", "aresample=async=1" # Key fix for AV drift
            ]
        else:
             pass

        cmd += [
            "-c:v", "libx264", 
            "-profile:v", "main", 
            "-pix_fmt", "yuv420p",
            # CBR mode for stable streaming (prevents buffer accumulation)
            "-b:v", self.config.bitrate,
            "-maxrate", self.config.bitrate,
            "-bufsize", self.config.bitrate,  # Same as bitrate for tight control
            "-g", str(p.gop), 
            "-x264-params", f"scenecut=0:keyint={p.gop}:min-keyint={p.gop}:nal-hrd=cbr", 
            "-tune", p.tune, 
            "-preset", p.preset,
            "-f", "flv", self.config.rtmp_url
        ]
        
        # Add extra user flags
        cmd.extend(p.extra_flags)

        print(f"[Streamer:{self.name}] Starting FFmpeg: {' '.join(cmd)}")
        
        self.process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, # Redirect stderr to stdout to avoid deadlock
            universal_newlines=True,
            bufsize=1
        )
        
        self.status.running = True
        self.status.pid = self.process.pid
        self.start_time = time.time()
        
        # Parse output line by line
        if self.process.stdout:
            for line in self.process.stdout:
                if self.stop_event.is_set():
                    break
                self._parse_progress(line.strip())
        
        self.process.wait()
        self.status.running = False
        self.status.pid = None

    def _parse_progress(self, line: str):
        parts = line.split("=", 1)
        if len(parts) != 2:
            return
        
        k, v = parts[0].strip(), parts[1].strip()
        
        updated = False
        if k == "fps":
            try:
                self.status.fps = float(v)
                updated = True
            except: pass
        elif k == "bitrate":
            self.status.bitrate = v
            updated = True
        elif k == "speed":
            self.status.speed = v
            updated = True
        elif k == "frame":
            try:
                self.status.frame = int(v)
                updated = True
            except: pass
        elif k == "progress" and v == "continue":
            self.status.uptime = time.time() - self.start_time
            self.status.last_update = time.time()
            self._write_status()

    def _write_status(self):
        # Write status file if configured
        if self.config.status_file:
            path = self.config.status_file
            try:
                tmp = path + ".tmp"
                with open(tmp, "w") as f:
                    f.write(self.status.model_dump_json())
                os.replace(tmp, path)
            except:
                pass
