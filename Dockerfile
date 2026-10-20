ARG BASE_IMAGE=mcr.microsoft.com/devcontainers/base:ubuntu-22.04
FROM ${BASE_IMAGE}

# Odoo runs from the host-built venv (VENV), mounted at its host path.
# Its interpreter is /usr/bin/python3.12 (deadsnakes) and its compiled C-extensions
# (python-ldap) link Jammy's OpenLDAP 2.5, so match the host distro and provide those libs.
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
        fonts-liberation && \
    rm -rf /var/lib/apt/lists/*

# wkhtmltopdf with patched qt, the build of Odoo's own image. Jammy's has no --header-html nor
# --footer-html, and fails every PDF report test with "Exit with code 1, due to unknown error".
RUN curl -fsSL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.jammy_amd64.deb \
        -o /tmp/wkhtmltox.deb && \
    echo "4f723b2691ad8638a9df960e0421d346d7315083e3583a334f33362280ddba15  /tmp/wkhtmltox.deb" | sha256sum -c - && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/wkhtmltox.deb && \
    rm -f /tmp/wkhtmltox.deb && \
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

# The postgres server of a bundle, which runs in its container (devcontainer/README.md, "How it
# runs Odoo"). PGDG for the 16 of the host and pgvector. No cluster of the package: a bundle starts
# from a copy of /opt/bundle-pg/16, made here once, so its first start runs no initdb.
# template_vector is the one the team skills create enterprise DBs from. Readable by all: the -uid
# image gives vscode the uid of the host user, which makes the copy.
ARG PG_USER
RUN install -d /usr/share/postgresql-common/pgdg /etc/postgresql-common && \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc]" \
        "https://apt.postgresql.org/pub/repos/apt jammy-pgdg main" > /etc/apt/sources.list.d/pgdg.list && \
    echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.conf && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        postgresql-16 postgresql-16-pgvector && \
    rm -rf /var/lib/apt/lists/* && \
    test -n "$PG_USER" && \
    install -d -o vscode /opt/bundle-pg && \
    runuser -u vscode -- /usr/lib/postgresql/16/bin/initdb -D /opt/bundle-pg/16 -U "$PG_USER" \
        -E UTF8 --locale=C.UTF-8 --lc-collate=C --wal-segsize=1 --auth=trust -N && \
    printf '%s\n' \
        "listen_addresses = ''" \
        "unix_socket_directories = '/var/run/postgresql'" \
        "shared_buffers = 64MB" \
        "synchronous_commit = off" \
        "wal_level = minimal" \
        "max_wal_senders = 0" \
        "min_wal_size = 4MB" \
        "max_wal_size = 128MB" \
        "wal_recycle = off" \
        "jit = off" \
        "max_parallel_workers_per_gather = 0" \
        "log_min_messages = warning" \
        >> /opt/bundle-pg/16/postgresql.conf && \
    runuser -u vscode -- /usr/lib/postgresql/16/bin/pg_ctl -D /opt/bundle-pg/16 -o "-k /tmp" -w start && \
    runuser -u vscode -- psql -h /tmp -U "$PG_USER" -d postgres \
        -c "CREATE DATABASE template_vector IS_TEMPLATE true" && \
    runuser -u vscode -- psql -h /tmp -U "$PG_USER" -d template_vector -c "CREATE EXTENSION vector" && \
    runuser -u vscode -- /usr/lib/postgresql/16/bin/pg_ctl -D /opt/bundle-pg/16 -m fast -w stop && \
    chmod -R a+rX /opt/bundle-pg

# Persist VS Code's extensions and the Claude extension's storage: devcontainer.json bind-mounts
# host dirs at ~/.vscode-server/extensions and in ~/.vscode-server/data/User/globalStorage.
# Pre-create the parents here owned by vscode so podman mounts the subdirs into existing dirs
# instead of auto-creating them as root. Otherwise the server's `mkdir ~/.vscode-server/bin` at
# attach fails with "Permission denied" (vscode can't write a root-owned parent, and
# no-new-privileges/cap-drop ALL means no runtime sudo to fix it).
RUN mkdir -p /home/vscode/.vscode-server/extensions \
        /home/vscode/.vscode-server/data/User/globalStorage && \
    chown -R vscode:vscode /home/vscode/.vscode-server

# Same root-owned-parent problem for ~/.local/share/Odoo and ~/.cache/warden, which devcontainer.json
# bind-mounts: pre-create the parents owned by vscode, so the tools writing next to those mounts
# can still mkdir there.
RUN mkdir -p /home/vscode/.local/share /home/vscode/.local/state /home/vscode/.cache && \
    chown -R vscode:vscode /home/vscode/.local /home/vscode/.cache

# ~/.bash_aliases, so the container's ~/.bashrc, install-claude-path.sh and the Bash( ... )
# permission rules keep reaching the shell helpers by that name while the mount itself is the
# directory holding them. Built here rather than in postCreateCommand so it exists before the
# first shell, and because a fragment can overwrite that scalar but not an image layer. The link
# dangles until the mount arrives, which is safe: the -uid image runs `chown -R` over this and
# -R implies no-dereference, so a broken link is skipped rather than fatal. Last in the file so
# changing the arg cannot invalidate the layers above.
ARG CONTAINER_BASHRC
RUN test -n "$CONTAINER_BASHRC" && \
    ln -sfn "$CONTAINER_BASHRC" /home/vscode/.bash_aliases && \
    chown -h vscode:vscode /home/vscode/.bash_aliases

# The venv's prompt in interactive shells; containerEnv already puts it first in PATH.
ARG VENV
RUN test -n "$VENV" && echo "source $VENV/bin/activate" >> /home/vscode/.bashrc
