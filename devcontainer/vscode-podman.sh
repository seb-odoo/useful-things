#!/bin/bash
# Replay the start event Dev Containers waits for, as the extension's podman events can subscribe after podman run.
if [ "$1" = events ] && [[ " $* " == *" event=start "* ]]; then
    exec podman "$@" --since "$(date -d '-2 sec' --iso-8601=seconds)"
fi
exec podman "$@"
