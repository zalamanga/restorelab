#!/bin/sh
set -eu
for directory in /archive /backups /recovery; do
    marker="$directory/.restorelab-volume"
    expected="restorelab-synthetic-v1:$directory"
    if [ -f "$marker" ] && [ ! -L "$marker" ]; then
        [ "$(cat "$marker")" = "$expected" ] || exit 31
    else
        [ ! -L "$directory" ] || exit 32
        [ -z "$(find "$directory" -mindepth 1 -maxdepth 1 -print -quit)" ] || exit 33
        printf '%s\n' "$expected" > "$marker"
    fi
    chown postgres:postgres "$directory" "$marker"
    chmod 700 "$directory"
done
