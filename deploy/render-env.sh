#!/usr/bin/env bash
# Prints the contents of .env.prod on stdout, from environment variables. CI runs this on the
# runner with the repository's GitHub secrets and variables and sends the output to the server
# over SSH (stdin, so no secret is ever on a command line or in the log):
#
#   bash deploy/render-env.sh | ssh <vm> 'umask 077; cat > .env.prod.new && mv .env.prod.new .env.prod'
#
# It refuses to print anything if a value is missing or looks wrong, so a forgotten or empty secret
# fails the deploy here, with the NAME of the problem value and never the value itself.
set -euo pipefail

fail() {
  echo "::error::$1" >&2
  exit 1
}

# Secrets, from the repository's GitHub secrets.
SECRETS="DB_URL JWT_SECRET RESEND_API_KEY REDIS_PASSWORD"
# Settings that are not secret, from the environment's variables.
SETTINGS="API_HOSTNAME FRONTEND_URL"

# shellcheck disable=SC1003 # the '\' pattern below is a literal backslash
for name in $SECRETS $SETTINGS; do
  value=${!name-}
  [ -n "$value" ] || fail "$name is empty: add it in GitHub (Settings, Secrets and variables, Actions)"
  # One line, and none of the characters Docker Compose would interpret in an env file.
  case "$value" in
    *[[:space:]]* | *'$'* | *'"'* | *"'"* | *'\'* | *'#'*)
      fail "$name contains a space, quote, backslash, # or \$, which an env file cannot hold safely"
      ;;
  esac
done

[[ $DB_URL == postgresql+asyncpg://* ]] || fail "DB_URL must start with postgresql+asyncpg://"
[[ $DB_URL == *ssl=require* ]] || fail "DB_URL must end with ?ssl=require (the database needs SSL)"
[ "${#JWT_SECRET}" -ge 32 ] || fail "JWT_SECRET must be at least 32 characters"
[[ $REDIS_PASSWORD =~ ^[A-Za-z0-9_-]{16,}$ ]] ||
  fail "REDIS_PASSWORD must be 16 or more letters, digits, - or _ (it goes into a URL)"
[[ $API_HOSTNAME =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] ||
  fail "API_HOSTNAME must be a host name such as animeplanet-api.duckdns.org (no https://, no path)"
# https, or http://localhost while the frontend is still being built locally.
[[ $FRONTEND_URL == https://* || $FRONTEND_URL =~ ^http://localhost(:[0-9]+)?/?$ ]] ||
  fail "FRONTEND_URL must start with https:// (or be http://localhost:PORT while testing)"

# Optional: more origins allowed to call the API from a browser, comma-separated. `none` or empty
# means no extra origins.
extra_origins=${CORS_EXTRA_ORIGINS-}
[ "$extra_origins" = none ] && extra_origins=
if [ -n "$extra_origins" ]; then
  IFS=, read -r -a origin_list <<< "$extra_origins"
  for origin in "${origin_list[@]}"; do
    [[ $origin =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] ||
      fail "CORS_EXTRA_ORIGINS must be comma-separated origins like http://localhost:3000"
  done
fi


cat <<EOF
# Written by CI (deploy/render-env.sh) from GitHub secrets and variables. Do not edit.
ENVIRONMENT=production
HTTP_SECURE=true
# none: a frontend on another site (localhost, Vercel) can keep the login cookie; it needs Secure
COOKIE_SAMESITE=none
LOG_JSON=true

DB_URL=$DB_URL
JWT_SECRET=$JWT_SECRET
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
RESEND_API_KEY=$RESEND_API_KEY
FRONTEND_URL=${FRONTEND_URL%/}
CORS_EXTRA_ORIGINS=$extra_origins

REDIS_PASSWORD=$REDIS_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/0
REDIS_BROKER_URL=redis://:$REDIS_PASSWORD@redis:6379/1
REDIS_BACKEND_URL=redis://:$REDIS_PASSWORD@redis:6379/2

CACHE_ENABLED=true
CACHE_TTL_SECONDS=3600
CACHE_STATS_TOP_N=1000

API_HOSTNAME=$API_HOSTNAME
EOF
