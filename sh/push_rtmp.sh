#!/usr/bin/env bash
set -euo pipefail

# ====== FIFO  ======
NAME="game"           # 區分多路可改名字
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
STREAM_KEY="102"
VHOST="prod.slot"
TOKEN="Ez5LG89M7dWVs1syXeHwsEvnJuFJAu7R"

OUT_URL="${STREAM_BASE%/}/${STREAM_KEY}?vhost=${VHOST}&token=${TOKEN}"

#VIDEO="/dev/video0"
VIDEO="/dev/v4l/by-id/usb-MACROSILICON_USB3_Video_20210623-video-index0"
VIDEO_SIZE="640x480"

AUDIO="plughw:CARD=Video,DEV=0"

CRF="18"           # 18(畫質好) ~ 30 (畫質差)
GOV="30"           # 搭配 fps
PRESET="ultrafast" # ultrafast, superfast, veryfast, faster

CMD=(/usr/bin/ffmpeg
	-hide_banner
	-nostats
	-loglevel warning
	-progress "$FIFO"
	-flags low_delay

	-fflags +genpts -use_wallclock_as_timestamps 1
	-f v4l2 -thread_queue_size 1024 -video_size "$VIDEO_SIZE" -i "$VIDEO"
	-f alsa -thread_queue_size 4096 -i "$AUDIO"
	-c:v libx264 -profile:v main -crf "$CRF" -pix_fmt yuv420p
	-g "$GOV" -x264-params scenecut=0:keyint=20:min-keyint=20
	-tune zerolatency -preset "$PRESET"
	-c:a aac -b:a 96k
	-f flv "$OUT_URL"
)
echo "Executing: ${CMD[*]}" # 這樣才能在日誌中看到完整命令
exec "${CMD[@]}"
