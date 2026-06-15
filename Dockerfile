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
