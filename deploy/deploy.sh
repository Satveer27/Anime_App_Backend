#!/usr/bin/env bash
# Recreate the backend stack (API, Celery worker and beat, Redis, Caddy) on the Oracle
# VM with the newest code. CI runs this over SSH after `git pull`, only when the backend changed.
# By hand:
#
#   bash deploy/deploy.sh            build the checked-out commit and deploy it
#   bash deploy/deploy.sh <tag>      roll back to an image that is already built (anime-backend:<tag>)
#
# Migrations are NOT done here: the `migrate` job in CI does them, when an Alembic file changed.
#   1. build the image (the running containers keep running)
#   2. recreate the containers and wait until they report healthy
#   3. if the new containers do not come up, put the previous image back
#   4. reload Caddy's config, check the worker, free old images
# A failure in step 1 leaves the running version exactly as it was.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose --env-file .env.prod -f docker-compose.prod.yml"
IMAGE=anime-backend

on_error() {
  echo "::error::deploy failed at line $1" >&2
  # Only the container list: this output may land in a public CI log, and container logs can hold
  # connection details. On the server: docker compose -f docker-compose.prod.yml logs <service>
  $COMPOSE ps || true
}
trap 'on_error $LINENO' ERR

[ -f .env.prod ] || {
  echo "::error::.env.prod is missing: CI writes it from GitHub secrets and variables" >&2
  exit 1
}

# What runs now, so a failed deploy can put it back.
previous_image=$(docker inspect --format '{{.Config.Image}}' anime_backend_app 2> /dev/null || true)
previous_tag=${previous_image##*:}

if [ $# -ge 1 ]; then
  export IMAGE_TAG=$1
  echo "== 1. Rolling back to $IMAGE:$IMAGE_TAG (no build)"
  docker image inspect "$IMAGE:$IMAGE_TAG" > /dev/null 2>&1 || {
    echo "::error::image $IMAGE:$IMAGE_TAG does not exist on this server" >&2
    docker image ls "$IMAGE" --format '  available: {{.Tag}}' >&2 || true
    exit 1
  }
else
  export IMAGE_TAG
  IMAGE_TAG=$(git rev-parse --short=12 HEAD)
  echo "== 1. Building $IMAGE:$IMAGE_TAG"
  $COMPOSE build app
fi

echo "== 2. Recreating the containers"
if ! $COMPOSE up -d --remove-orphans --wait --wait-timeout "${DEPLOY_WAIT_TIMEOUT:-240}"; then
  echo "::error::the new containers did not become healthy" >&2
  $COMPOSE ps || true
  if [ -n "$previous_tag" ] && [ "$previous_tag" != "$IMAGE_TAG" ] &&
    docker image inspect "$IMAGE:$previous_tag" > /dev/null 2>&1; then
    echo "== 3. Putting $IMAGE:$previous_tag back" >&2
    IMAGE_TAG=$previous_tag $COMPOSE up -d --remove-orphans --wait --wait-timeout "${DEPLOY_WAIT_TIMEOUT:-240}" ||
      echo "::error::the previous version did not come back healthy either" >&2
  else
    echo "== 3. No previous image to go back to" >&2
  fi
  exit 1
fi

echo "== 4. Reloading Caddy and checking the stack"
# The Caddyfile is bind-mounted, which a recreate does not notice: tell Caddy to reload it.
$COMPOSE exec -T caddy caddy reload --config /etc/caddy/Caddyfile --force > /dev/null 2>&1 || true

echo "-- API readiness"
$COMPOSE exec -T app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).read().decode())"

echo "-- Celery worker connected to the broker"
worker_ok=0
for _ in $(seq 1 12); do
  if $COMPOSE exec -T celery_worker .venv/bin/celery -A app.core.workers.celery_worker \
    inspect ping -t 10 > /dev/null 2>&1; then
    worker_ok=1
    break
  fi
  sleep 5
done
[ "$worker_ok" = 1 ] || {
  echo "::error::no Celery worker answered a ping" >&2
  exit 1
}
echo "worker answered"

# Keep the five newest images (for rollback), drop the rest and anything dangling.
docker image ls "$IMAGE" --format '{{.Tag}}' | tail -n +6 | xargs -r -I{} docker rmi "$IMAGE:{}" > /dev/null 2>&1 || true
docker image prune -f > /dev/null

echo "== Done: $IMAGE:$IMAGE_TAG"
$COMPOSE ps
