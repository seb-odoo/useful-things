#!/bin/bash
set -u

module=$1
checks_before_reload=3
checks_between_reloads=6
state=${WIFI_WATCHDOG_STATE:-/run/wifi-watchdog}
mkdir -p "$state"

if [ "$(nmcli networking) $(nmcli radio wifi)" != "enabled enabled" ]; then
    rm -f "$state/offline" "$state/reloaded"
    exit 0
fi

if [ -n "$(ip -4 route show default)" ]; then
    if [ -e "$state/reloaded" ]; then
        echo "online again since the reload of $(cat "$state/reloaded")"
    fi
    rm -f "$state/offline" "$state/reloaded"
    exit 0
fi

offline=$(( $(cat "$state/offline" 2>/dev/null || echo 0) + 1 ))
if [ "$offline" -lt "$checks_before_reload" ]; then
    echo "$offline" > "$state/offline"
    exit 0
fi

echo "still no default route with the wifi on: reloading $module"
timeout 60 modprobe -r "$module"
timeout 60 modprobe "$module"
echo $(( checks_before_reload - checks_between_reloads )) > "$state/offline"
date +%H:%M > "$state/reloaded"
