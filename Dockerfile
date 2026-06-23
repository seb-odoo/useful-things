FROM mcr.microsoft.com/devcontainers/base:ubuntu-22.04

# Odoo runs from the host-built venv mounted at /home/seb/virtualenvs/odoo20.
# Its interpreter is /usr/bin/python3.12 (deadsnakes) and its compiled C-extensions
# (python-ldap) link Jammy's OpenLDAP 2.5 — so match the host distro and provide those libs.
RUN apt-get update && \
    apt-get install -y --no-install-recommends software-properties-common gnupg ca-certificates && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-venv \
        libldap-2.5-0 \
        libsasl2-2 \
        postgresql-client \
        fonts-dejavu-core \
        fonts-liberation \
        wkhtmltopdf && \
    rm -rf /var/lib/apt/lists/*

# Chrome for HttpCase/tour (browser_js) + websocket tests. Odoo's ChromeBrowser runs it headless
# with --no-sandbox --disable-dev-shm-usage, so it works under the dropped caps / small /dev/shm.
RUN curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
        -o /tmp/google-chrome.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/google-chrome.deb && \
    rm -f /tmp/google-chrome.deb && \
    rm -rf /var/lib/apt/lists/*

# Node.js for Odoo's JS tooling (eslint/prettier/lint-staged; package.json engines >= 16.11).
# Jammy's apt nodejs is v12 (too old) so install Node 22 LTS from NodeSource. Build-time only:
# no-new-privileges + non-root vscode user means runtime apt/sudo can't escalate.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Persist VS Code's extensions: devcontainer.json bind-mounts a host dir at
# ~/.vscode-server/extensions. Pre-create the parent here owned by vscode so podman mounts the subdir
# into an existing dir instead of auto-creating ~/.vscode-server as root — otherwise the server's
# `mkdir ~/.vscode-server/bin` at attach fails with "Permission denied" (vscode can't write a
# root-owned parent, and no-new-privileges/cap-drop ALL means no runtime sudo to fix it).
RUN mkdir -p /home/vscode/.vscode-server/extensions && \
    chown -R vscode:vscode /home/vscode/.vscode-server
