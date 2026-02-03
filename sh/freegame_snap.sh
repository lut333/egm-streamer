#!/usr/bin/env bash
set -euo pipefail

log() { echo "[$(date -Is)] $*"; }

# 參數支援兩種呼叫方式：
# A) freegame_snap.sh rtmp://...             (預設 watch)
# B) freegame_snap.sh watch rtmp://... [out] (顯式模式)
MODE="watch"
URL=""
OUT_FILE="/dev/shm/freegame/snap.jpg"

if [[ $# -ge 1 ]]; then
	if [[ "$1" =~ ^(rtmp|http|https):// ]]; then
		URL="$1"
		MODE="${MODE:-watch}"
		OUT_FILE="${2:-$OUT_FILE}"
	else
		MODE="$1"
		URL="${2:-}"
		OUT_FILE="${3:-$OUT_FILE}"
	fi
fi

# 可用環境變數覆蓋
SCALE="${SCALE:-640:-1}"    # 例如 640:-1 或 480:-1
INTERVAL="${INTERVAL:-1.0}" # watch 模式每幾秒抓一次
RW_TIMEOUT_US="${RW_TIMEOUT_US:-5000000}"

# JPEG 品質：數字越小品質越好(檔更大/CPU略高)，數字越大品質越差(檔更小/更省)
Q_ONESHOT="${Q_ONESHOT:-3}"
Q_WATCH="${Q_WATCH:-14}" # 你想再更省：可改 18~24（畫質會更糊）

OUT_DIR="$(dirname "$OUT_FILE")"
TMP_FILE="${OUT_FILE}.tmp"

if [[ -z "${URL// /}" ]]; then
	log "[ERROR] URL is empty"
	exit 1
fi
if [[ -z "${OUT_FILE// /}" ]]; then
	log "[ERROR] OUT_FILE is empty"
	exit 1
fi

mkdir -p "$OUT_DIR"

snap_once() {
	local q="$1"
	# 先寫 tmp，再原子覆蓋，避免 classifier 讀到半張
	/usr/bin/ffmpeg -hide_banner -loglevel error -y \
		-rw_timeout "$RW_TIMEOUT_US" \
		-i "$URL" \
		-an -sn -dn \
		-frames:v 1 \
		-vf "scale=${SCALE}" \
		-q:v "$q" \
		-f image2 -vcodec mjpeg \
		"$TMP_FILE"

	mv -f "$TMP_FILE" "$OUT_FILE"
}

log "mode=$MODE url=$URL out=$OUT_FILE scale=$SCALE interval=$INTERVAL q_watch=$Q_WATCH"

case "$MODE" in
oneshot)
	snap_once "$Q_ONESHOT"
	log "OK (oneshot): $OUT_FILE"
	;;
watch)
	while true; do
		snap_once "$Q_WATCH" || log "[WARN] snap failed rc=$?"
		sleep "$INTERVAL"
	done
	;;
*)
	log "[ERROR] unknown mode=$MODE (use watch|oneshot)"
	exit 1
	;;
esac
