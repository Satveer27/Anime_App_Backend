#!/usr/bin/env bash
# Load-test matrix for issue #55: the API runs on this machine against RDS (.env.rds) with a
# local Redis, once with the cache off and once with it on, at several concurrency levels.
#
#   bash analysis/run_load_matrix.sh OUT_DIR "5 20 50" 60 45
#
# Needs: `docker compose up -d redis`, and the venv from `uv sync --group dev`.
# Read-only against RDS. The cache keys are cleared before every run, so each level starts cold.
set -u
OUT=${1:?output dir}
LEVELS=${2:-"5 20 50"}
FULL_SECONDS=${3:-60}
STATS_SECONDS=${4:-45}
PORT=8000
HOST=http://127.0.0.1:$PORT
PY=.venv/Scripts/python.exe
mkdir -p "$OUT"

redis() { docker exec anime_redis redis-cli "$@"; }
clear_cache() { redis --scan --pattern 'cache:v1:*' | tr -d '\r' | xargs -r -n 100 docker exec anime_redis redis-cli del >/dev/null; }
stat() { redis info stats | tr -d '\r' | grep "^$1:" | cut -d: -f2; }

free_port() {
  powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force }" >/dev/null 2>&1
  sleep 2
}

start_server() {  # $1 = true|false (cache enabled)
  if curl -sf $HOST/api/v1/health >/dev/null 2>&1; then
    echo "port $PORT is already in use: refusing to measure the wrong server"; return 1
  fi
  APP_ENV=rds CACHE_ENABLED=$1 $PY -m uvicorn app.main:app --port $PORT --log-level warning \
    >"$OUT/server_cache_$1.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 60); do
    curl -sf $HOST/api/v1/health >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "server did not start"; return 1
}
stop_server() {
  free_port  # the shell's PID is not the Windows PID, so stop whatever listens on the port
  curl -sf $HOST/api/v1/health >/dev/null 2>&1 && { echo "server still running"; exit 1; }
}

run_one() {  # $1 mode  $2 mix  $3 users  $4 seconds
  clear_cache
  local hits0 miss0 hits1 miss1 prefix="$OUT/$1_$2_$3"
  hits0=$(stat keyspace_hits); miss0=$(stat keyspace_misses)
  $PY -m locust -f analysis/locustfile.py "$2" --headless -u "$3" -r "$3" -t "${4}s" \
    --host $HOST --csv "$prefix" --only-summary >"$prefix.out" 2>&1
  hits1=$(stat keyspace_hits); miss1=$(stat keyspace_misses)
  {
    echo "keyspace_hits=$((hits1 - hits0))"
    echo "keyspace_misses=$((miss1 - miss0))"
    echo "cache_keys=$(redis --scan --pattern 'cache:v1:*' | wc -l)"
    echo "used_memory_bytes=$(redis info memory | tr -d '\r' | grep '^used_memory:' | cut -d: -f2)"
    echo "anime_statistics_keys=$(redis --scan --pattern 'cache:v1:anime_statistics:*' | wc -l)"
  } >"$prefix.redis"
  echo "done $1 $2 $3"
}

for mode in uncached cached; do
  flag=false; [ "$mode" = cached ] && flag=true
  start_server $flag || exit 1
  for users in $LEVELS; do
    run_one $mode FullMix "$users" "$FULL_SECONDS"
    run_one $mode StatsOnly "$users" "$STATS_SECONDS"
  done
  stop_server
done
clear_cache
echo "all done"
