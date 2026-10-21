# Odoo dev reverse proxy

Run several Odoo dev containers in parallel and reach each from the host browser by name:

- `http://<bundle>.localhost` — e.g. `http://master-owl-3-props-3--seb.localhost`, `http://19.0.localhost`
- `http://<anything>.<bundle>.localhost` — arbitrary subdomains (website/domain testing)
- `http://bug.<bundle>.localhost` and `http://fix.<bundle>.localhost` go to ports 8070 and 8071
  of the container instead: a bug fix shown side by side, the code before the fix and with it,
  each on its own server and database (`bug-fix.sh` of the serve-for-seb skill). Two host names
  also keep two sessions, so Seb never logs in again when he switches tab.

A single rootless nginx container sits on the `podman` network, listens on `127.0.0.1:80`, and
routes by `Host` header to each dev container's IP on port 8069, on a network the two share: when
the dev containers live on another network, the proxy joins it by a quadlet drop-in
(`odoo-proxy.container.d/`). The config is generated
from the live container list by [`../scripts/proxy.py`](../scripts/proxy.py): each dev container
is labelled `devcontainer.local_folder=<bundle path>`, so the subdomain is the bundle folder
basename. A basename that is no bundle name (`BUNDLE_NAME` in `scripts/commands.py`) gets no route:
a `;` or a `}` in it would add directives to the config. A `podman events` watcher regenerates the config whenever a container starts or stops,
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
podman kill -s HUP odoo-proxy                         # reload nginx on the current file
podman logs odoo-proxy                                # proxy logs
systemctl --user status odoo-proxy-watch.service      # watcher status
```

## Notes

- **Threaded mode** (Odoo's dev default): `/websocket` is served on 8069, so the proxy sends it
  there. If you run multi-worker (`--workers>0`), websockets move to the gevent port 8072 —
  change the `/websocket` `proxy_pass` port in `proxy.py` to 8072.
- `proxy_mode = 1` is already set in [`../.odoorc-dev`](../.odoorc-dev), required behind a proxy.
- VS Code's own port forwarding is disabled for 8069/8072 in
  [`../devcontainer/base.jsonc`](../devcontainer/base.jsonc) (`portsAttributes`) so windows stop
  fighting over the same host port: access goes through this proxy instead.
  `remote.autoForwardPorts: false` (in `customizations.vscode.settings`) is needed to actually
  stop VS Code forwarding: `portsAttributes: ignore` alone lets a remembered forward be
  restored; also remove the lingering forward in the Ports panel and reload once.
- Odoo master defaults `--http-interface` to `127.0.0.1`, so Odoo binds loopback-only and the
  proxy gets 502 (connection refused). `odoo-bin-params` in `container-rw/devcontainer.bashrc`
  appends `--http-interface=0.0.0.0 --proxy-mode` (and `--with-demo` where the branch has it) for
  in-container server runs, guarded by `ODOO_PROXY_HOST` and skipping the `s`/`p` editions; the
  host keeps the safe 127.0.0.1 default. The container loads no `.odoorc-dev`, so these go on the
  launch command, not in the config. `odoo-bin` prints `URL: http://admin.<bundle>.localhost` on
  real server runs only.
- `ODOO_PROXY_HOST = ${localWorkspaceFolderBasename}` (= the proxy subdomain) is set in
  `containerEnv`: like `portsAttributes`, it only applies after a container rebuild.
- Long bundle names overflow nginx's default 64-byte server-name hash, so `regen` prepends
  `server_names_hash_bucket_size 128;` (an http-context directive, valid in a `conf.d/*.conf`
  include). Dotted bundle names like `19.0` work verbatim in `server_name`.
- The nginx template (`scripts/proxy.py`, `_render`) follows Odoo's deploy doc: `proxy_redirect
  off`, `proxy_read/send_timeout 720s`, `client_max_body_size 100M`, `proxy_buffering off`, gzip,
  `proxy_http_version 1.1` on both `/` and `/websocket`.
- The containers share the host's Postgres, so they mount the host filestore as their default
  data_dir (`SHARE_ROOT/Odoo`); `keep-id` maps writes to the host user. A database with
  attachment rows and no files anywhere (a runbot dump restored without its filestore) is a data
  gap the mount cannot fix, and `debug=assets` serving empty bundles reproduces on a direct
  `:8069` too: Odoo-side, not the proxy.
