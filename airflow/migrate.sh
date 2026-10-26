#!/usr/bin/env bash
# Bring the anime database (RDS) to the latest Alembic revision, with the code checked out right
# now. CI runs this over SSH on every push to main, whether or not Airflow itself changed.
# You can also run it by hand: bash airflow/migrate.sh
#
#   1. build the image: the migration needs a Python environment that matches the new code (a new
#      model can import a new library). Docker's cache makes this take seconds when nothing
#      relevant changed, and it never restarts a running container.
#   2. `alembic upgrade head`, then `alembic check`, inside that image, against RDS.
#
# The running Airflow keeps using its old containers while this runs, so a migration must work with
# the old code too: add columns and tables, do not drop or rename in the same deploy.
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.prod.yml"

echo "== Building the image (cached when nothing relevant changed)"
$COMPOSE build

echo "== Migrating RDS: alembic upgrade head, then alembic check"
# -T: no terminal. Over SSH from CI there is none, and `run` would otherwise fail without one.
$COMPOSE --profile migrate run --rm -T migrate < /dev/null
echo "== RDS is at the latest revision"
