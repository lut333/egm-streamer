#!/usr/bin/env bash
set -euo pipefail

# =========================
# 推流 status 來源目錄
# =========================
DIRS=(
	"/dev/shm/ffmpeg/game.progress"
	"/dev/shm/ffmpeg/cam.progress"
)

# =========================
# 截圖監控設定
# =========================
SNAP_SERVICE="freegame-snap.service"
SNAP_FILE="/dev/shm/freegame/snap.jpg"
SNAP_STALE_SEC=3 # 超過幾秒沒更新就視為卡住（可依FPS 調整）

# =========================
# Freegame classifier 監控設定
# =========================
CLASS_TIMER="freegame.timer"
CLASS_SERVICE="freegame.service"
CLASS_STATUS="/dev/shm/freegame/freegame.status"
# timer 是 OnUnitActiveSec=2s，所以 2~3 輪沒更新就算怪怪的
CLASS_STALE_SEC=6

# =========================
# drop/min 狀態暫存（建議放 /dev/shm，避免 /run 權限/清理問題）
# =========================
STATE_DIR="/dev/shm/watch-streams-state"
mkdir -p "$STATE_DIR"

# =========================
# 工具函式
# =========================
ts() { date '+%F %T'; }
now_epoch() { date +%s; }
mtime_epoch() { stat -c %Y "$1" 2>/dev/null || echo 0; }
size_bytes() { stat -c %s "$1" 2>/dev/null || echo 0; }

svc_state() {
	local svc="$1"
	if systemctl is-active --quiet "$svc" 2>/dev/null; then
		echo "active"
	else
		echo "inactive"
	fi
}

unit_state() {
	local u="$1"
	if systemctl is-active --quiet "$u" 2>/dev/null; then
		echo "active"
	else
		echo "inactive"
	fi
}

norm_bitrate_num() {
	local br="$1"
	br="${br%kbits/s}"
	br="${br%bits/s}"
	br="${br%kb/s}"
	br="${br%k}"
	[[ -z "$br" ]] && br="0"
	printf "%s" "$br"
}

norm_speed_num() {
	local sp="$1"
	sp="${sp%x}"
	[[ -z "$sp" ]] && sp="0"
	awk -v s="$sp" 'BEGIN{ printf "%.2f", s }'
}

calc_drop_per_min() {
	local key="$1" drop="$2" now="$3"
	local f="${STATE_DIR}/${key}.state"
	local prev_ts=0 prev_drop=-1

	if [[ -f "$f" ]]; then
		read -r prev_ts prev_drop <"$f" || true
	fi

	# 覆寫狀態（不會長大）
	printf "%s %s\n" "$now" "$drop" >"$f"

	# 第一次/重啟(drop變小) → 0
	if [[ "$prev_ts" -le 0 || "$prev_drop" -lt 0 || "$drop" -lt "$prev_drop" ]]; then
		echo "0.0"
		return
	fi

	local dt=$((now - prev_ts))
	local dd=$((drop - prev_drop))
	if [[ "$dt" -le 0 || "$dd" -lt 0 ]]; then
		echo "0.0"
		return
	fi

	awk -v dd="$dd" -v dt="$dt" 'BEGIN{ printf "%.1f", (dd*60.0)/dt }'
}

# 取 status line 裡的 key=value
get_kv() {
	local line="$1" key="$2" def="${3:-}"
	local kv k v
	for kv in $line; do
		k="${kv%%=*}"
		v="${kv#*=}"
		if [[ "$k" == "$key" ]]; then
			echo "$v"
			return 0
		fi
	done
	echo "$def"
}

# =========================
# 排版：兩個區塊清楚分隔
# =========================
W=96
hr() { printf '%*s\n' "$W" '' | tr ' ' '-'; }
title() {
	local s="$1"
	hr
	printf "[ %s ]\n" "$s"
	hr
}

# =========================
# 主程式
# =========================
echo "[$(ts)]"
echo

# ---------- STREAM HEALTH ----------
title "STREAM HEALTH"

shopt -s nullglob
files=()
for d in "${DIRS[@]}"; do
	files+=("$d"/*.status)
done

NOW="$(now_epoch)"

if ((${#files[@]} == 0)); then
	echo "No stream status files found."
else
	rows=()
	for f in "${files[@]}"; do
		name="$(basename "$f" .status)"
		label="${name^^}"
		line="$(<"$f")"

		# defaults
		t="N/A"
		fps="0"
		speed="0x"
		br="0"
		dup="0"
		drop="0"
		frame="0"

		for kv in $line; do
			key="${kv%%=*}"
			val="${kv#*=}"
			case "$key" in
			t) t="$val" ;;
			fps) fps="$val" ;;
			speed) speed="$val" ;;
			br) br="$val" ;;
			dup) dup="$val" ;;
			drop) drop="$val" ;;
			frame) frame="$val" ;;
			esac
		done

		spn="$(norm_speed_num "$speed")"
		brn="$(norm_bitrate_num "$br")"

		state_key="$(echo "${f%/*}_${name}" | tr '/:.' '___')"
		dpm="$(calc_drop_per_min "$state_key" "$drop" "$NOW")"

		mtime="$(mtime_epoch "$f")"
		age=$((NOW - mtime))
		((age < 0)) && age=0

		case "$label" in
		GAME) sk="0_${label}" ;;
		CAM) sk="1_${label}" ;;
		*) sk="2_${label}" ;;
		esac

		# sk|label|t|fps|spn|brn|dup|drop|dpm|frame|age
		rows+=("${sk}|${label}|${t}|${fps}|${spn}|${brn}|${dup}|${drop}|${dpm}|${frame}|${age}")
	done

	IFS=$'\n' rows=($(printf "%s\n" "${rows[@]}" | sort))
	unset IFS

	{
		printf "NAME\tOUT_TIME\tFPS\tSPD\tBR(kbps)\tDUP\tDROP\tDROP/min\tFRAME\tAGE(s)\n"
		for r in "${rows[@]}"; do
			IFS="|" read -r sk label t fps sp brn dup drop dpm frame age <<<"$r"
			unset IFS
			printf "%s\t%s\t%.2f\t%.2f\t%.1f\t%s\t%s\t%.1f\t%s\t%s\n" \ 
			"$label" "$t" "${fps:-0}" "${sp:-0}" "${brn:-0}" "$dup" "$drop" "${dpm:-0}" "$frame" "$age"
		done
	} | column -t -s $'\t'

	echo
	echo "備註：AGE(s)=status 檔最後更新距離現在幾秒；若持續變大，代表該路 progress 可能卡住/停止更新。"
fi

echo

# ---------- CAPTURE HEALTH ----------
title "CAPTURE HEALTH"

snap_svc="$(svc_state "$SNAP_SERVICE")"

if [[ -f "$SNAP_FILE" ]]; then
	snap_age=$((NOW - $(mtime_epoch "$SNAP_FILE")))
	((snap_age < 0)) && snap_age=0
	snap_size="$(size_bytes "$SNAP_FILE")"
	snap_file="$(basename "$SNAP_FILE")"

	if [[ "$snap_age" -le "$SNAP_STALE_SEC" && "$snap_size" -gt 0 ]]; then
		snap_health="UPDATING"
	else
		snap_health="STALE"
	fi
else
	snap_age="N/A"
	snap_size="0"
	snap_file="$(basename "$SNAP_FILE")"
	snap_health="MISSING"
fi

{
	printf "CAPTURE\tSERVICE\tFILE\tSIZE(B)\tAGE(s)\tHEALTH\n"
	printf "FREEGAME_SNAP\t%s\t%s\t%s\t%s\t%s\n" \ 
	"$snap_svc" "$snap_file" "$snap_size" "$snap_age" "$snap_health"
} | column -t -s $'\t'

echo
echo "備註：HEALTH=UPDATING 表示 snap.jpg 正在持續覆寫更新；STALE 表示超過 ${SNAP_STALE_SEC}s 沒更新（可依 FPS 調整）。"
echo " 路徑：${SNAP_FILE}"
echo

# ---------- FREEGAME CLASSIFIER ----------
title "FREEGAME CLASSIFIER"

timer_state="$(unit_state "$CLASS_TIMER")"
svc_state2="$(unit_state "$CLASS_SERVICE")"

if [[ -f "$CLASS_STATUS" ]]; then
	st_age=$((NOW - $(mtime_epoch "$CLASS_STATUS")))
	((st_age < 0)) && st_age=0
	st_file="$(basename "$CLASS_STATUS")"
	st_line="$(tail -n 1 "$CLASS_STATUS" 2>/dev/null || true)"

	st_state="$(get_kv "$st_line" state "UNKNOWN")"
	st_pending="$(get_kv "$st_line" pending "0")"
	st_pnone="$(get_kv "$st_line" pending_none "0")"
	st_sel_mean="$(get_kv "$st_line" select_mean "-")"
	st_play_mean="$(get_kv "$st_line" play_mean "-")"
	st_obs_s="$(get_kv "$st_line" obs_select "-")"
	st_obs_p="$(get_kv "$st_line" obs_play "-")"

	if [[ "$st_age" -le "$CLASS_STALE_SEC" ]]; then
		st_health="FRESH"
	else
		st_health="STALE"
	fi
else
	st_age="N/A"
	st_file="$(basename "$CLASS_STATUS")"
	st_state="MISSING"
	st_pending="-"
	st_pnone="-"
	st_sel_mean="-"
	st_play_mean="-"
	st_obs_s="-"
	st_obs_p="-"
	st_health="MISSING"
fi

{
	printf "UNIT(timer)\tUNIT(service)\tSTATUS_FILE\tAGE(s)\tHEALTH\tSTATE\tSELECT_MEAN\tPLAY_MEAN\tPENDING\tPENDING_NONE\tOBS_S\tOBS_P\n"
	printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \ 
	"$timer_state" "$svc_state2" "$st_file" "$st_age" "$st_health" "$st_state" \ 
	"$st_sel_mean" "$st_play_mean" "$st_pending" "$st_pnone" "$st_obs_s" "$st_obs_p"
} | column -t -s $'\t'

echo
echo "備註："
echo " - HEALTH=FRESH 表示 freegame.status 近期有更新；timer 是 2s/輪，超過 ${CLASS_STALE_SEC}s 多半表示 classifier 沒在跑或卡住。"
echo " - STATE=SELECT/PLAY/NONE/UNKNOWN 取自 freegame.status。"
echo " - 若用了 pending 版本，PENDING=1 代表正在吞過場動畫（維持 SELECT 不掉 NONE）。"
