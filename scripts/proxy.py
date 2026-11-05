"""Generate the nginx reverse-proxy config for the running Odoo dev containers.

Each dev container is opened from a bundle folder under WORKTREE_CONTAINER and Podman labels
it with ``devcontainer.local_folder=<bundle path>``. We route ``http://<bundle>.localhost``
(and ``*.<bundle>.localhost``) to that container's bridge IP on port 8069, and
``bug.<bundle>.localhost`` and ``fix.<bundle>.localhost`` to the ports of ``SIDES``, and
``mail.<bundle>.localhost`` to the Mailpit of the container (``container-rw/mailpit.sh``).

Usage:
 $ python proxy.py regen     # rebuild the config once from `podman ps` and reload nginx
 $ python proxy.py watch     # regen on every container start/stop (run as a systemd-user unit)
"""

import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

from commands import BUNDLE_NAME

CONTAINER_NAME = "odoo-proxy"
DEVCONTAINER_LABEL = "devcontainer.local_folder"
HTTP_PORT = 8069
# The ports bug-fix.sh of the serve-for-seb skill serves the two sides on.
SIDES = {"bug": 8070, "fix": 8071}
MAILPIT_PORT = 8025
CONF_PATH = Path.home() / ".config" / "odoo-proxy" / "odoo-dev.conf"


def _proxy_networks():
    """The networks the proxy is on, by name: it reaches a dev container on one they share."""
    res = subprocess.run(
        ["podman", "inspect", "--format", "{{json .NetworkSettings.Networks}}", CONTAINER_NAME],
        capture_output=True,
        text=True,
    )
    try:
        networks = json.loads(res.stdout or "{}") if res.returncode == 0 else {}
    except ValueError:
        return {}
    return networks if isinstance(networks, dict) else {}


def _running_dev_containers(shared):
    """Yield (subdomain, ip) for every running dev container, newest last, but one whose folder
    name is no bundle name: it would add directives to the config of nginx. `shared`: the networks
    of the proxy, where its address is looked up first."""
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
        ).stdout,
    )
    for container in info:
        labels = container.get("Config", {}).get("Labels") or {}
        folder = labels.get(DEVCONTAINER_LABEL)
        if not folder:
            continue
        networks = container.get("NetworkSettings", {}).get("Networks") or {}
        addresses = [
            network.get("IPAddress")
            for name, network in sorted(networks.items(), key=lambda pair: pair[0] not in shared)
        ]
        ip = next(filter(None, addresses), None)
        name = os.path.basename(folder.rstrip("/"))
        if not ip or not BUNDLE_NAME.fullmatch(name):
            continue
        yield name, ip


def _render(server_name, ip, port=HTTP_PORT, websocket="/websocket"):
    # Dots in bundle names (e.g. 19.0) are valid in server_name, so no sanitizing is needed.
    # Directives follow Odoo's recommended nginx reverse-proxy config (deploy docs).
    return f"""server {{
    listen 80;
    server_name {server_name};

    proxy_read_timeout 720s;
    proxy_send_timeout 720s;
    proxy_redirect off;
    client_max_body_size 100M;
    proxy_buffering off;
    gzip on;
    gzip_types text/css text/plain text/xml application/xml application/json application/javascript image/svg+xml;

    location ~ ^/(web/database/|xmlrpc/(2/)?db/?$) {{
        return 403;
    }}

    location {websocket} {{
        # Threaded dev: websockets ride on {port}; under --workers>0 switch this to 8072.
        proxy_pass http://{ip}:{port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }}

    location / {{
        proxy_pass http://{ip}:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }}
}}
"""


def _clients(networks):
    """The lines that let the host alone use the proxy: its published port comes from loopback,
    from the gateway of a network of the proxy, or from the address of the proxy itself there
    (podman's port forwarder connects from inside the container: 10.89.255.30 on 2026-11-05), and
    any other client is refused, as a dev container would reach the server of another bundle by
    its Host (security check 11-03, C7)."""
    allowed = ["127.0.0.1", "::1"]
    for network in networks.values():
        for key in ("Gateway", "IPAddress"):
            try:
                allowed.append(str(ipaddress.ip_address(network[key])))
            except (KeyError, TypeError, ValueError):
                continue
    return "".join(f"allow {address};\n" for address in dict.fromkeys(allowed)) + "deny all;\n"


def regen():
    """Rebuild the nginx config from the live container list and reload the proxy."""
    blocks, networks = [], _proxy_networks()
    for subdomain, ip in _running_dev_containers(networks):
        blocks.append(_render(f"{subdomain}.localhost *.{subdomain}.localhost", ip))
        blocks += [
            _render(f"{side}.{subdomain}.localhost", ip, port) for side, port in SIDES.items()
        ]
        blocks.append(_render(f"mail.{subdomain}.localhost", ip, MAILPIT_PORT, "/api/events"))
    # Bundle names are long, so the default 64-byte server-name hash bucket overflows.
    header = f"server_names_hash_bucket_size 128;\n{_clients(networks)}\n"
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
