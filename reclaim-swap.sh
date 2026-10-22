#!/bin/bash
set -eu

# sudo needs a terminal for the password, and hctl runs this without one.
if [ ! -t 0 ]; then
    log=$(mktemp)
    gnome-terminal --wait --title "reclaim swap: sudo" -- bash -c 'bash "$0" 2>&1 | tee "$1"; echo "${PIPESTATUS[0]}" > "$1.status"; read -rsn1 -t 60 -p "press a key to close"' "$(realpath "$0")" "$log" || true
    cat "$log"
    status=$(cat "$log.status" 2>/dev/null || echo 1)
    rm -f "$log" "$log.status"
    exit "$status"
fi

read -r used available < <(awk '/^MemAvailable:/ {a = $2} /^SwapTotal:/ {t = $2} /^SwapFree:/ {f = $2} END {print t - f, a}' /proc/meminfo)
free -h
if [ "$used" -eq 0 ]; then
    echo "nothing in swap"
    exit 0
fi
if [ $((used + 2 * 1024 * 1024)) -gt "$available" ]; then
    echo "$((used / 1024)) MB in swap, $((available / 1024)) MB of RAM available: close something first, swapoff needs room for all of it plus 2 GB"
    exit 1
fi
sudo swapoff -a
sudo swapon -a
free -h
