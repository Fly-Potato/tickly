#!/bin/sh
set -eu
cleanup() { docker compose down --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
docker compose config --quiet
docker compose build
docker compose up --detach
for service in api web; do
  remaining=60
  while [ "$remaining" -gt 0 ]; do
    container_id="$(docker compose ps --quiet "$service")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    [ "$health" = healthy ] && break
    [ "$health" = unhealthy ] && exit 1
    sleep 1
    remaining=$((remaining - 1))
  done
  [ "$health" = healthy ] || exit 1
done
curl --fail --silent http://127.0.0.1:8080/ | grep -q 'id="root"'
test "$(curl --fail --silent http://127.0.0.1:8080/health)" = '{"status":"ok"}'
test "$(curl --fail --silent http://127.0.0.1:8080/ready)" = '{"status":"ready"}'
printf '%s\n' "Docker smoke test passed"
