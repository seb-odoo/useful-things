# Odoo dev reverse proxy

Run several Odoo dev containers in parallel and reach each from the host browser by name:

- `http://<bundle>.localhost` — e.g. `http://master-owl-3-props-3--seb.localhost`, `http://19.0.localhost`
- `http://<anything>.<bundle>.localhost` — arbitrary subdomains (website/domain testing)

A single rootless nginx container sits on the `podman` network, listens on `127.0.0.1:80`, and
routes by `Host` header to each dev container's bridge IP on port 8069. The config is generated
from the live container list by [`../scripts/proxy.py`](../scripts/proxy.py): each dev container
is labelled `devcontainer.local_folder=<bundle path>`, so the subdomain is the bundle folder
basename. A `podman events` watcher regenerates the config whenever a container starts or stops,
so routes (and changing bridge IPs) stay in sync automatically.

## One-time host setup

1. **Allow rootless bind to port 80** (so URLs need no `:port`):

   ```bash
   echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/99-odoo-proxy.conf
   sudo sysctl --system
   ```

   (No-root alternative: drop this, change `PublishPort` in `odoo-proxy.container` to
   `127.0.0.1:8080:80`, and use `http://<bundle>.localhost:8080`.)

2. **Seed the config and install the units:**

   ```bash
   python3 ~/repo/useful-things/scripts/proxy.py regen   # creates ~/.config/odoo-proxy/odoo-dev.conf
   mkdir -p ~/.config/containers/systemd ~/.config/systemd/user
   ln -sfn ~/repo/useful-things/proxy/odoo-proxy.container ~/.config/containers/systemd/odoo-proxy.container
   ln -sfn ~/repo/useful-things/proxy/odoo-proxy-watch.service ~/.config/systemd/user/odoo-proxy-watch.service
   systemctl --user daemon-reload
   systemctl --user start odoo-proxy.service
   systemctl --user enable --now odoo-proxy-watch.service
   loginctl enable-linger "$USER"        # keep them running after logout
   ```

   The `odoo-dev.conf` file must exist before the container starts (step 2 runs `regen` first),
   otherwise Podman would create a directory at the mount path.

3. **DNS.** Chromium-based browsers resolve `*.localhost` (and deeper `a.b.localhost`) to
   `127.0.0.1` with zero config. For Firefox / `curl`, add a dnsmasq wildcard:
   `address=/localhost/127.0.0.1`.

## Day to day

Nothing — start a dev container and the watcher adds its route; stop it and the route is removed.

```bash
python3 ~/repo/useful-things/scripts/proxy.py regen   # force a rebuild
cat ~/.config/odoo-proxy/odoo-dev.conf                # inspect current routes
podman logs odoo-proxy                                # proxy logs
systemctl --user status odoo-proxy-watch.service      # watcher status
```

## Notes

- **Threaded mode** (Odoo's dev default): `/websocket` is served on 8069, so the proxy sends it
  there. If you run multi-worker (`--workers>0`), websockets move to the gevent port 8072 —
  change the `/websocket` `proxy_pass` port in `proxy.py` to 8072.
- `proxy_mode = 1` is already set in [`../.odoorc-dev`](../.odoorc-dev), required behind a proxy.
- VS Code's own port forwarding is disabled for 8069/8072 in the shared
  [`../devcontainer.json`](../devcontainer.json) (`portsAttributes`) so windows stop fighting
  over the same host port — access goes through this proxy instead.
