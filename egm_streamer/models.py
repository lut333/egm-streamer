from typing import List, Optional, Dict, Literal
from pydantic import BaseModel, Field

class ROI(BaseModel):
    name: str
    x: int
    y: int
    w: int
    h: int
    required: bool = False

class MatchPolicy(BaseModel):
    min_match: int = 1
    max_match: int = 3
    threshold: int = 12

class StateConfig(BaseModel):
    name: Optional[str] = None  # NORMAL, SELECT, PLAYING (Optional, usually inferred from dict key)
    refs_dir: str
    rois: List[ROI]
    # Direct config (matches config.example.yaml)
    min_match: int = 1
    threshold: int = 12
    # Alias for compatibility
    exclude_if_match: List[str] = Field(default_factory=list)
    
    @property
    def match_policy(self) -> MatchPolicy:
        """Convert direct fields to MatchPolicy for backward compatibility"""
        return MatchPolicy(min_match=self.min_match, threshold=self.threshold)

class StreamConfig(BaseModel):
    url: Optional[str] = None
    capture_interval: float = 1.0
    scale: str = "640:-1"
    rw_timeout_us: int = 5000000
    quality: int = 2 # FFmpeg -q:v (1-31, 1 is best)

class DetectionConfig(BaseModel):
    algo: Literal["phash", "dhash", "ahash"] = "dhash"
    hash_size: int = 8
    samples: int = 3
    sample_interval: float = 0.15

class DebounceConfig(BaseModel):
    confirm_frames: int = 2
    drop_frames: int = 6

class OutputConfig(BaseModel):
    status_file: str = "/dev/shm/state.json"

class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080

class FFmpegParams(BaseModel):
    preset: str = "ultrafast"
    tune: str = "zerolatency"
    gop: int = 30
    extra_flags: List[str] = []

class StreamerConfig(BaseModel):
    enabled: bool = False
    input_device: str = "/dev/video0"
    audio_device: Optional[str] = None
    resolution: str = "640x480"
    fps: int = 30
    bitrate: str = "2000k"
    rtmp_url: Optional[str] = None
    ffmpeg_params: FFmpegParams = Field(default_factory=FFmpegParams)
    
    # Internal usage or override
    status_file: Optional[str] = None 

class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    client_id: Optional[str] = None # Optional override, otherwise use common.instance_id

class DetectorConfigWrapper(BaseModel):
    enabled: bool = True
    target_stream: str = "game"  # Name of the stream to capture from
    
    capture: StreamConfig = Field(default_factory=lambda: StreamConfig(url=""))
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    states: Dict[str, StateConfig] = Field(default_factory=dict)
    priority: List[str] = ["SELECT", "PLAYING", "NORMAL"]
    debounce: DebounceConfig = Field(default_factory=DebounceConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    
    # Notifications
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

class CommonConfig(BaseModel):
    instance_id: str = "egm-default"
    log_level: str = "INFO"

class SnapshotServiceConfig(BaseModel):
    enabled: bool = False
    target_stream: str = "game"
    url: Optional[str] = None # Optional override
    output_path: str = "/dev/shm/latest.jpg"
    interval: float = 1.0
    quality: int = 2 # FFmpeg -q:v

class AppConfig(BaseModel):
    common: CommonConfig = Field(default_factory=CommonConfig)
    streams: Dict[str, StreamerConfig] = Field(default_factory=dict)
    # Default detector to disabled if not provided
    detector: DetectorConfigWrapper = Field(default_factory=lambda: DetectorConfigWrapper(enabled=False, states={}))
    snapshot: SnapshotServiceConfig = Field(default_factory=SnapshotServiceConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)

# Runtime Models
class MatchResult(BaseModel):
    state: str
    matched_rois: List[str]
    avg_distance: float
    is_match: bool

class DetectionResult(BaseModel):
    state: str
    matches: Dict[str, MatchResult]
    timestamp: float

class StreamStatus(BaseModel):
    name: str # Stream name e.g. "game", "cam"
    running: bool
    pid: Optional[int]
    fps: float
    bitrate: str
    speed: str = "0x"
    frame: int = 0
    uptime: float
    last_update: float
