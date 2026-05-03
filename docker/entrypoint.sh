#!/bin/sh
set -eu

: "${RCODER_MODEL:?RCODER_MODEL is required}"
: "${RCODER_BASE_URL:?RCODER_BASE_URL is required}"
: "${RCODER_API_KEY:?RCODER_API_KEY is required}"
: "${RCODER_BOOTSTRAP_ACCESS_SECRET:?RCODER_BOOTSTRAP_ACCESS_SECRET is required}"
: "${RCODER_ADMIN_ACCESS_SECRET:?RCODER_ADMIN_ACCESS_SECRET is required}"

CONFIG_PATH="${RCODER_CONFIG_PATH:-/app/.rcoder/config.host.yaml}"
CONFIG_DIR="$(dirname "$CONFIG_PATH")"

mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_PATH" ]; then
  envsubst < /app/docker/config.host.yaml.template > "$CONFIG_PATH"
fi

if [ -n "${EZCODE_DATABASE_URL:-}" ] && [ "${EZCODE_AUTO_MIGRATE:-true}" = "true" ]; then
  rcoder --config "$CONFIG_PATH" db migrate
fi

exec rcoder --config "$CONFIG_PATH" --server
