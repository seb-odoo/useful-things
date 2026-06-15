"""Generate the nginx reverse-proxy config for the running Odoo dev containers.

Each dev container is opened from a bundle folder under WORKTREE_CONTAINER and Podman labels
it with ``devcontainer.local_folder=<bundle path>``. We route ``http://<bundle>.localhost``
(and ``*.<bundle>.localhost``) to that container's bridge IP on port 8069.

Usage:
 $ python proxy.py regen     # rebuild the config once from `podman ps` and reload nginx
 $ python proxy.py watch     # regen on every container start/stop (run as a systemd-user unit)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CONTAINER_NAME = "odoo-proxy"
DEVCONTAINER_LABEL = "devcontainer.local_folder"
HTTP_PORT = 8069
CONF_PATH = Path.home() / ".config" / "odoo-proxy" / "odoo-dev.conf"


def _running_dev_containers():
    """Yield (subdomain, ip) for every running dev container, newest last."""
    ids = subprocess.run(
        ["podman", "ps", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    if not ids:
        return
    info = json.loads(
        subprocess.run(
            ["podman", "inspect", *ids],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    for container in info:
        labels = container.get("Config", {}).get("Labels") or {}
        folder = labels.get(DEVCONTAINER_LABEL)
        if not folder:
            continue
        networks = container.get("NetworkSettings", {}).get("Networks") or {}
        ip = (networks.get("podman") or {}).get("IPAddress") or container.get(
            "NetworkSettings", {}
        ).get("IPAddress")
        if not ip:
            continue
        yield os.path.basename(folder.rstrip("/")), ip


def _render(subdomain, ip):
    # Dots in `subdomain` (e.g. 19.0) are valid in server_name, so no sanitizing is needed.
    # Directives follow Odoo's recommended nginx reverse-proxy config (deploy docs).
    return f"""server {{
    listen 80;
    server_name {subdomain}.localhost *.{subdomain}.localhost;

    proxy_read_timeout 720s;
    proxy_send_timeout 720s;
    proxy_redirect off;
    client_max_body_size 100M;
    proxy_buffering off;
    gzip on;
    gzip_types text/css text/plain text/xml application/xml application/json application/javascript image/svg+xml;

    location /websocket {{
        # Threaded dev: websockets ride on {HTTP_PORT}; under --workers>0 switch this to 8072.
        proxy_pass http://{ip}:{HTTP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }}

    location / {{
        proxy_pass http://{ip}:{HTTP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }}
}}
"""


def regen():
    """Rebuild the nginx config from the live container list and reload the proxy."""
    blocks = [_render(subdomain, ip) for subdomain, ip in _running_dev_containers()]
    # Bundle names are long, so the default 64-byte server-name hash bucket overflows.
    header = "server_names_hash_bucket_size 128;\n\n"
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONF_PATH.write_text(header + "\n".join(blocks))
    # Reload without restart; no-op (rc != 0) when the proxy is not running yet.
    subprocess.run(
        ["podman", "kill", "-s", "HUP", CONTAINER_NAME],
        capture_output=True,
        text=True,
    )
    print(f"Wrote {len(blocks)} server block(s) to {CONF_PATH}")


def watch():
    """Regen on every container start/stop so routes follow live containers and IPs."""
    regen()
    proc = subprocess.Popen(
        [
            "podman",
            "events",
            "--filter",
            "type=container",
            "--filter",
            "event=start",
            "--filter",
            "event=died",
            "--format",
            "{{.ID}}",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    for _line in proc.stdout:
        regen()


COMMANDS = {"regen": regen, "watch": watch}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(COMMANDS)}}}")
    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
