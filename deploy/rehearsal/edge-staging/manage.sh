#!/usr/bin/env bash
set -euo pipefail

readonly DEVICE_ID="edge-staging-ubuntu-01"
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly COMPOSE_FILE="${SCRIPT_DIR}/compose.yml"
readonly ENV_FILE="${SCRIPT_DIR}/.env"
readonly STATE_FILE="${SCRIPT_DIR}/.deployment.env"
readonly PREVIOUS_STATE_FILE="${SCRIPT_DIR}/.previous-deployment.env"
readonly SERVICE="edge-staging"
readonly CONTAINER_NAME="senior-pomidor-edge-staging"

usage() {
  cat <<'EOF'
Usage: ./manage.sh COMMAND [ARGS]

Commands:
  deploy DIGEST_REF COMMIT_SHA  Pull and deploy a verified immutable image
  start                         Start the configured deployment
  stop                          Intentionally stop the container
  restart                       Restart the running container
  version                       Verify configured and running image identity
  logs [COMPOSE LOGS ARGS...]   Show container logs
  spool-status                  Show the durable telemetry spool status
  rollback                      Deploy the previously recorded image
  reset-data [DEVICE_ID]        Archive persistent data after exact confirmation
EOF
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

validate_image() {
  local image="$1"
  [[ "$image" =~ ^ghcr\.io/[a-z0-9]+([._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$ ]] ||
    fail "image must be ghcr.io/<repository>@sha256:<64 lowercase hex>; tags are forbidden"
}

validate_commit() {
  local commit="$1"
  [[ "$commit" =~ ^[0-9a-fA-F]{40}$ ]] || fail "commit SHA must contain exactly 40 hexadecimal characters"
}

require_env() {
  [[ -f "$ENV_FILE" ]] || fail "missing ${ENV_FILE}; copy .env.example and provision staging-only credentials"
}

require_state() {
  [[ -f "$STATE_FILE" ]] || fail "no deployment is configured; run deploy first"
}

load_state() {
  local file="$1"
  local key value
  EDGE_IMAGE=""
  EDGE_COMMIT=""
  while IFS='=' read -r key value; do
    case "$key" in
      EDGE_IMAGE) EDGE_IMAGE="$value" ;;
      EDGE_COMMIT) EDGE_COMMIT="$value" ;;
      "") ;;
      *) fail "unexpected key in state file: ${key}" ;;
    esac
  done <"$file"
  [[ -n "$EDGE_IMAGE" ]] || fail "missing EDGE_IMAGE in state file"
  [[ -n "$EDGE_COMMIT" ]] || fail "missing EDGE_COMMIT in state file"
  validate_image "$EDGE_IMAGE"
  validate_commit "$EDGE_COMMIT"
}

compose() {
  require_env
  docker compose --project-directory "$SCRIPT_DIR" --env-file "$ENV_FILE" --env-file "$STATE_FILE" \
    -f "$COMPOSE_FILE" "$@"
}

validate_compose_inputs() {
  local image="$1"
  EDGE_IMAGE="$image" docker compose --project-directory "$SCRIPT_DIR" --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" config --quiet
}

image_revision() {
  docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$1"
}

verify_revision() {
  local image="$1"
  local expected="$2"
  local actual
  actual="$(image_revision "$image")"
  [[ "${actual,,}" == "${expected,,}" ]] ||
    fail "OCI revision mismatch for ${image}: expected ${expected}, got ${actual:-<empty>}"
}

write_state() {
  local image="$1"
  local commit="$2"
  local next_state previous_state
  next_state="$(mktemp "${SCRIPT_DIR}/.deployment.env.tmp.XXXXXX")"
  previous_state=""
  printf 'EDGE_IMAGE=%s\nEDGE_COMMIT=%s\n' "$image" "$commit" >"$next_state"
  chmod 0600 "$next_state"

  if [[ -f "$STATE_FILE" ]]; then
    previous_state="$(mktemp "${SCRIPT_DIR}/.previous-deployment.env.tmp.XXXXXX")"
    cp "$STATE_FILE" "$previous_state"
    chmod 0600 "$previous_state"
    mv -f "$previous_state" "$PREVIOUS_STATE_FILE"
  fi
  mv -f "$next_state" "$STATE_FILE"
}

deploy() {
  [[ $# -eq 2 ]] || fail "deploy requires DIGEST_REF and COMMIT_SHA"
  local image="$1"
  local commit="$2"
  validate_image "$image"
  validate_commit "$commit"
  require_env

  validate_compose_inputs "$image"
  docker pull "$image"
  verify_revision "$image" "$commit"
  write_state "$image" "${commit,,}"
  mkdir -p "$SCRIPT_DIR/data"
  compose config --quiet
  compose up -d --no-build
  version
}

version() {
  require_state
  load_state "$STATE_FILE"
  local configured_image="$EDGE_IMAGE"
  local expected_commit="$EDGE_COMMIT"
  local container_id running_image running_image_id configured_image_id running_commit
  container_id="$(compose ps -q "$SERVICE")"
  [[ -n "$container_id" ]] || fail "the staging container does not exist"
  running_image="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
  running_image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
  configured_image_id="$(docker image inspect --format '{{.Id}}' "$configured_image")"
  running_commit="$(image_revision "$running_image_id")"

  printf 'configured_digest=%s\n' "$configured_image"
  printf 'running_digest=%s\n' "$running_image"
  printf 'configured_image_id=%s\n' "$configured_image_id"
  printf 'running_image_id=%s\n' "$running_image_id"
  printf 'configured_commit=%s\n' "$expected_commit"
  printf 'running_commit=%s\n' "$running_commit"

  [[ "$running_image" == "$configured_image" ]] || fail "running digest differs from configured digest"
  [[ "$running_image_id" == "$configured_image_id" ]] || fail "running image ID differs from configured image ID"
  [[ "${running_commit,,}" == "${expected_commit,,}" ]] || fail "running OCI revision differs from configured commit"
}

rollback() {
  [[ -f "$PREVIOUS_STATE_FILE" ]] || fail "no previous deployment is recorded"
  load_state "$PREVIOUS_STATE_FILE"
  local previous_image="$EDGE_IMAGE"
  local previous_commit="$EDGE_COMMIT"
  deploy "$previous_image" "$previous_commit"
}

reset_data() {
  local confirmation="${1:-}"
  if [[ -z "$confirmation" ]]; then
    read -r -p "Type ${DEVICE_ID} to archive all staging data: " confirmation
  fi
  [[ "$confirmation" == "$DEVICE_ID" ]] || fail "confirmation did not exactly match ${DEVICE_ID}"

  if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    docker stop "$CONTAINER_NAME" >/dev/null
  fi
  if [[ ! -e "$SCRIPT_DIR/data" ]]; then
    echo "No data directory exists; nothing to archive."
    return
  fi

  local timestamp backup
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="${SCRIPT_DIR}/data.backup-${timestamp}"
  [[ ! -e "$backup" ]] || backup="${backup}-$$"
  mv "$SCRIPT_DIR/data" "$backup"
  mkdir -p "$SCRIPT_DIR/data"
  printf 'Archived staging data to %s\n' "$backup"
}

command="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$command" in
  deploy) deploy "$@" ;;
  start) require_state; compose up -d --no-build ;;
  stop) require_state; compose stop ;;
  restart) require_state; compose restart ;;
  version) version ;;
  logs) require_state; compose logs "$@" ;;
  spool-status) require_state; compose exec -T "$SERVICE" python scripts/telemetry_spool.py status ;;
  rollback) rollback ;;
  reset-data) reset_data "$@" ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
