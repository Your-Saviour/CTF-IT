#!/bin/bash
# Monitord - System Metrics Daemon

LOGFILE=/var/log/monitord/monitord.log
PORT=9000

log() { echo "$(date '+%Y-%m-%d %T') $*" >> "$LOGFILE"; }

collect_cpu() {
    awk '/cpu / {usage=100-($5*100/($2+$3+$4+$5+$6+$7+$8)); printf "cpu_usage=%.1f\n", usage}' /proc/stat
}

collect_mem() {
    awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf "mem_used_kb=%d\nmem_total_kb=%d\n", t-a, t}' /proc/meminfo
}

collect_disk() {
    df -h / | awk 'NR==2{printf "disk_used=%s\ndisk_total=%s\ndisk_pct=%s\n", $3, $2, $5}'
}

handle_request() {
    local query="$1"
    case "$query" in
        cpu)  collect_cpu ;;
        mem)  collect_mem ;;
        disk) collect_disk ;;
        *)    echo "unknown metric" ;;
    esac
}

log "monitord starting on port $PORT"

while true; do
    request=$(echo "" | nc -l -p "$PORT" -q 1)
    response=$(handle_request "$(echo "$request" | tr -d '\r\n')")
    echo "$response" | nc -l -p "$PORT" -q 1 > /dev/null 2>&1 &
done
