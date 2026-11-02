#!/bin/bash
set -eu

# sudo needs a terminal for the password, and hctl runs this without one, nor a display.
if [ ! -t 0 ]; then
    log=$(mktemp)
    DISPLAY=${DISPLAY:-:0} XAUTHORITY=${XAUTHORITY:-/run/user/$(id -u)/gdm/Xauthority} gnome-terminal --wait --title "wifi watchdog: sudo" -- bash -c 'bash "$0" 2>&1 | tee "$1"; echo "${PIPESTATUS[0]}" > "$1.status"; read -rsn1 -t 60 -p "press a key to close"' "$(realpath "$0")" "$log" || true
    cat "$log"
    status=$(cat "$log.status" 2>/dev/null || echo 1)
    rm -f "$log" "$log.status"
    exit "$status"
fi

module=$(basename "$(readlink -f /sys/class/ieee80211/*/device/driver/module)")
[ -n "$module" ] || { echo "no wifi driver found"; exit 1; }

sudo install -m 755 "$(dirname "$(realpath "$0")")/wifi-watchdog-check.sh" /usr/local/sbin/wifi-watchdog
sudo tee /etc/systemd/system/wifi-watchdog.service > /dev/null <<EOF
[Unit]
Description=Reload the wifi driver when the wifi stays without network

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/wifi-watchdog $module
EOF
sudo tee /etc/systemd/system/wifi-watchdog.timer > /dev/null <<'EOF'
[Unit]
Description=Check the wifi every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now wifi-watchdog.timer
systemctl list-timers wifi-watchdog.timer --no-pager
