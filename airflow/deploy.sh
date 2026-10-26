#!/usr/bin/env bash
# Recreate the Airflow stack on the Oracle pipeline box with the newest code. CI runs this over SSH
# right after migrate.sh, and only when something under airflow/ changed. You can also run it by
# hand: bash airflow/deploy.sh
#
# Migrations are NOT done here: migrate.sh has already brought RDS up to date.
#   1. refuse to touch anything while a DAG run is active (a restart would kill its tasks)
#   2. build the image (a cache hit right after migrate.sh); the old containers keep running
#   3. recreate the containers on the new image
#   4. wait until Airflow answers, otherwise fail loudly
# A failure in step 1 or 2 leaves the running Airflow exactly as it was.
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.prod.yml"

on_error() {
  echo "::error::deploy failed at line $1" >&2
  # Only the container list: this output may land in a public CI log, and container logs can
  # contain connection details. Read them on the server: docker compose -f docker-compose.prod.yml logs
  $COMPOSE ps || true
}
trap 'on_error $LINENO' ERR

echo "== 1. Checking for an active DAG run"
if [ -n "$($COMPOSE ps -q postgres < /dev/null)" ]; then
  # The stack is up, so the answer must come from its database. If the database cannot be read
  # the guard cannot vouch for anything: the command fails and the deploy stops (never fail open).
  running=$($COMPOSE exec -T postgres psql -U airflow -d airflow -tAc \
    "select count(*) from dag_run where state = 'running'" < /dev/null | tr -d '[:space:]')
  case "$running" in
    '' | *[!0-9]*)
      echo "::error::could not read the number of running DAG runs (got '$running')" >&2
      exit 1
      ;;
  esac
else
  running=0 # nothing is running yet (first deploy): nothing to protect
fi
if [ "$running" != "0" ]; then
  echo "::error::$running DAG run(s) are running. Not restarting Airflow under them." >&2
  echo "Re-run this deploy when they have finished." >&2
  trap - ERR
  exit 1
fi

echo "== 2. Building the image"
$COMPOSE build

echo "== 3. Recreating the containers"
$COMPOSE up -d --force-recreate

echo "== 4. Waiting for Airflow to answer"
for _ in $(seq 1 60); do
  if curl -fsS --max-time 5 http://127.0.0.1:8080/api/v2/monitor/health > /dev/null 2>&1; then
    echo "Airflow is up"
    docker image prune -f > /dev/null # the previous build is now untagged: free its disk space
    $COMPOSE ps
    exit 0
  fi
  sleep 5
done
echo "::error::Airflow did not become healthy within 5 minutes" >&2
exit 1
