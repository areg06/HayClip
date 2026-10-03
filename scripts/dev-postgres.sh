#!/usr/bin/env bash
# Local development Postgres for HayClips: a private cluster under $HAYCLIPS_HOME (default ~/.hayclips),
# unix-socket only (no TCP listener), so nothing is reachable from the network.
#   scripts/dev-postgres.sh init|start|stop|status|psql
set -euo pipefail
HOME_DIR="${HAYCLIPS_HOME:-$HOME/.hayclips}"
DATA="$HOME_DIR/pgdata"
SOCK="$HOME_DIR/pgsock"
PORT="${HAYCLIPS_PG_PORT:-54329}"
case "${1:-status}" in
  init)
    mkdir -p "$SOCK"; chmod 700 "$HOME_DIR" "$SOCK"
    [ -d "$DATA" ] && { echo "already initialised: $DATA"; exit 0; }
    initdb -D "$DATA" -U hayclips --auth=trust -E UTF8 --locale=C >/dev/null
    cat >> "$DATA/postgresql.conf" <<CONF
listen_addresses = ''
unix_socket_directories = '$SOCK'
unix_socket_permissions = 0700
port = $PORT
CONF
    echo "initialised $DATA" ;;
  start)
    if pg_ctl -D "$DATA" status >/dev/null 2>&1; then
      echo "already running"
    else
      pg_ctl -D "$DATA" -l "$HOME_DIR/postgres.log" -w start >/dev/null
    fi
    createdb -h "$SOCK" -p "$PORT" -U hayclips hayclips 2>/dev/null || true
    echo "running: host=$SOCK port=$PORT dbname=hayclips user=hayclips" ;;
  stop)   pg_ctl -D "$DATA" -w stop ;;
  status) pg_ctl -D "$DATA" status || true ;;
  psql)   exec psql -h "$SOCK" -p "$PORT" -U hayclips hayclips ;;
  *) echo "usage: $0 init|start|stop|status|psql" >&2; exit 2 ;;
esac
