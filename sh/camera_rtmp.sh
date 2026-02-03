#!/usr/bin/env bash
set -euo pipefail

# ====== FIFO  ======
NAME="cam"            # 區分多路可改名字
DIR="/dev/shm/ffmpeg" # 有 RuntimeDirectory 就用這個
FIFO="${DIR}/${NAME}.progress.fifo"
STATUS="${DIR}/${NAME}.status"

mkdir -p "$DIR"
rm -f "$FIFO"
mkfifo -m 600 "$FIFO"

# 背景 parser：把 progress 轉成「永遠覆寫的單行 status」
awk -F= -v OUT="$STATUS" '
  function trim(s){ sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }

    BEGIN {
    print "starting..." > OUT
    close(OUT)
  }

  $1=="frame"       { frame=trim($2) }
  $1=="fps"         { fps=trim($2) }
  $1=="speed"       { sp=trim($2) }
  $1=="out_time"    { t=trim($2) }
  $1=="bitrate"     { br=trim($2) }
  $1=="dup_frames"  { dup=trim($2) }
  $1=="drop_frames" { drop=trim($2) }

  $1=="progress" {
    p=trim($2)
    if (p=="continue" || p=="end") {
      printf "t=%s frame=%s fps=%s speed=%s br=%s dup=%s drop=%s\n", t,frame,fps,sp,br,dup,drop > OUT
      close(OUT)  # 下一次用 > 覆寫
    }
  }
' <"$FIFO" &
PARSER_PID=$!

cleanup() {
	kill "$PARSER_PID" 2>/dev/null || true
	rm -f "$FIFO"
}
trap cleanup EXIT

# ====== ffmpeg  ======
STREAM_BASE="rtmp://192.168.43.10/game"
STREAM_KEY="102_cam"
VHOST="prod.slot"
TOKEN="Ez5LG89M7dWVs1syXeHwsEvnJuFJAu7R"

OUT_URL="${STREAM_BASE%/}/${STREAM_KEY}?vhost=${VHOST}&token=${TOKEN}"

VIDEO="/dev/v4l/by-id/usb-Generic_Rmoncam_5M_200901010001-video-index0"
VIDEO_SIZE="640x480"

CRF="18" # 省CPU可改 26~30；18 很吃CPU
GOP="30" # 30fps -> 60 (2秒一個 keyframe)
PRESET="ultrafast"

CMD=(/usr/bin/ffmpeg
	-hide_banner
	-nostats
	-loglevel warning
	-progress "$FIFO"

	-f v4l2 -thread_queue_size 1024
	-input_format mjpeg -video_size "$VIDEO_SIZE"
	-i "$VIDEO"
	-an
	-c:v libx264 -preset "$PRESET" -tune zerolatency
	-pix_fmt yuv420p -profile:v baseline
	-crf "$CRF"
	-g "$GOP" -keyint_min "$GOP" -sc_threshold 0
	-f flv "$OUT_URL"
)

echo "Executing: ${CMD[*]}"
exec "${CMD[@]}"
