#!/bin/sh
set -eu
source_file="$1"
wal_file="$2"
printf '%s\n' "$wal_file" | grep -Eq '^([0-9A-F]{24}|[0-9A-F]{24}\.[0-9A-F]{8}\.backup|[0-9A-F]{8}\.history)$' || exit 40
[ "$(cat /archive/.restorelab-volume)" = 'restorelab-synthetic-v1:/archive' ] || exit 42
system_id="$(pg_controldata "$PGDATA" | sed -n 's/^Database system identifier: *//p')"
[ "$(cat /archive/.restorelab-system-id)" = "$system_id" ] || exit 43
target="/archive/$wal_file"
if [ -e "$target" ]; then
    [ ! -L "$target" ] && cmp -s "$source_file" "$target"
else
    temporary="/archive/.$wal_file.$$"
    cp "$source_file" "$temporary"
    mv "$temporary" "$target"
fi
