#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_TAG="${ZETHERION_PYTHON_TOOL_IMAGE:-zetherion-ai-dev-tools:py312}"
DOCKERFILE_PATH="${ZETHERION_PYTHON_TOOL_DOCKERFILE:-$REPO_DIR/Dockerfile.dev-tools}"
EXPLICIT_ZETHERION_ENV_FILE="${ZETHERION_ENV_FILE:-}"
DEFAULT_ZETHERION_ENV_FILE="$REPO_DIR/.env"
HOST_WORKSPACE_ROOT="${ZETHERION_HOST_WORKSPACE_ROOT:-$REPO_DIR}"
WORKSPACE_MOUNT_TARGET="${ZETHERION_WORKSPACE_MOUNT_TARGET:-/workspace}"
SIBLING_CGS_ROOT_DEFAULT="$(cd "$REPO_DIR/.." && pwd)/catalyst-group-solutions"
SIBLING_CGS_MOUNT_TARGET="${ZETHERION_CGS_WORKSPACE_MOUNT_TARGET:-/workspace-siblings/catalyst-group-solutions}"

is_generated_e2e_env_file() {
    local env_file="${1:-}"
    case "$env_file" in
        */zetherion-e2e-runs/stacks/*/run.env|*/.artifacts/e2e-runs/stacks/*/run.env|*/.artifacts/ci-e2e-runs/stacks/*/run.env)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

map_repo_path_to_host() {
    local path="${1:-}"
    if [ -z "$path" ] || [ "$HOST_WORKSPACE_ROOT" = "$REPO_DIR" ]; then
        printf '%s\n' "$path"
        return 0
    fi
    case "$path" in
        "$REPO_DIR")
            printf '%s\n' "$HOST_WORKSPACE_ROOT"
            ;;
        "$REPO_DIR"/*)
            printf '%s/%s\n' "$HOST_WORKSPACE_ROOT" "${path#"$REPO_DIR"/}"
            ;;
        *)
            printf '%s\n' "$path"
            ;;
    esac
}

map_repo_path_to_container() {
    local path="${1:-}"
    if [ -z "$path" ]; then
        printf '%s\n' "$path"
        return 0
    fi
    case "$path" in
        "$REPO_DIR")
            printf '%s\n' "$WORKSPACE_MOUNT_TARGET"
            ;;
        "$REPO_DIR"/*)
            printf '%s/%s\n' "$WORKSPACE_MOUNT_TARGET" "${path#"$REPO_DIR"/}"
            ;;
        *)
            printf '%s\n' "$path"
            ;;
    esac
}

HOST_DOCKERFILE_PATH="$(map_repo_path_to_host "$DOCKERFILE_PATH")"
HOST_DEFAULT_ZETHERION_ENV_FILE="$(map_repo_path_to_host "$DEFAULT_ZETHERION_ENV_FILE")"

compute_tool_context_hash() {
    local files=(
        "$DOCKERFILE_PATH"
        "$REPO_DIR/requirements.txt"
        "$REPO_DIR/requirements-dev.txt"
        "$REPO_DIR/docs/requirements.txt"
    )
    local existing=()
    local file
    for file in "${files[@]}"; do
        if [ -f "$file" ]; then
            existing+=("$file")
        fi
    done
    if [ "${#existing[@]}" -eq 0 ]; then
        echo "missing-context"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 - "${existing[@]}" <<'PY'
import hashlib
import os
import sys

digest = hashlib.sha256()
for path in sys.argv[1:]:
    digest.update(os.path.basename(path).encode("utf-8"))
    digest.update(b"\0")
    with open(path, "rb") as handle:
        digest.update(handle.read())
    digest.update(b"\0")
print(digest.hexdigest())
PY
        return
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${existing[@]}" | sha256sum | awk '{print $1}'
        return
    fi
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${existing[@]}" | shasum -a 256 | awk '{print $1}'
        return
    fi
    echo "missing-hash-tool"
    return 1
}

build_image() {
    local context_hash current_hash rebuild_required
    context_hash="$(compute_tool_context_hash)"
    rebuild_required="${ZETHERION_PYTHON_TOOL_REBUILD:-false}"
    if ! docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
        rebuild_required="true"
    elif [ "$rebuild_required" != "true" ]; then
        current_hash="$(docker image inspect --format '{{ index .Config.Labels "zetherion.tool_context_hash" }}' "$IMAGE_TAG" 2>/dev/null || true)"
        if [ "$current_hash" != "$context_hash" ]; then
            rebuild_required="true"
        fi
    fi
    if [ "$rebuild_required" = "true" ]; then
        docker build \
            --label "zetherion.tool_context_hash=$context_hash" \
            -f "$HOST_DOCKERFILE_PATH" \
            -t "$IMAGE_TAG" \
            "$HOST_WORKSPACE_ROOT" >/dev/null
    fi
}

collect_env_args() {
    local name
    while IFS='=' read -r name _; do
        case "$name" in
            ANTHROPIC_*|API_*|APP_*|CGS_*|CI|COMPOSE_FILE|CURRENT_*|DISCORD_*|DOCKER_*|E2E_*|EMBEDDINGS_*|ENVIRONMENT|GEMINI_*|GITHUB_*|GROQ_*|HEAD_*|LOCAL_E2E_*|MISSING_ENV|OPENAI_*|OLLAMA_*|OWNER_*|POSTGRES_*|PROJECT|PRESERVE_TEST_VOLUMES|PYTEST_*|QDRANT_*|RECEIPT_*|RELEASE_*|ROUTER_*|RUN_*|SKILLS_*|SSL_CERT_FILE|STRICT_*|SUITE_*|TEST_*|VERSION|WRAPPER_*|ZETHERION_*)
                ENV_ARGS+=("-e" "$name")
                ;;
        esac
    done < <(env)
}

build_image

ENV_ARGS=(
    "-e" "PYTHONPATH=/workspace/src"
    "-e" "E2E_RUNTIME_HOST=${E2E_RUNTIME_HOST:-host.docker.internal}"
    "-e" "ZETHERION_HOST_WORKSPACE_ROOT=$HOST_WORKSPACE_ROOT"
    "-e" "ZETHERION_WORKSPACE_ROOT=$WORKSPACE_MOUNT_TARGET"
    "-e" "TMPDIR=/tmp"
    "-e" "TMP=/tmp"
    "-e" "TEMP=/tmp"
)

ENV_FILE_PATH=""
if [ -n "$EXPLICIT_ZETHERION_ENV_FILE" ]; then
    if [ ! -f "$EXPLICIT_ZETHERION_ENV_FILE" ]; then
        if is_generated_e2e_env_file "$EXPLICIT_ZETHERION_ENV_FILE"; then
            echo "WARN: Ignoring missing generated E2E env file: $EXPLICIT_ZETHERION_ENV_FILE" >&2
        else
            echo "ERROR: ZETHERION_ENV_FILE points to a missing file: $EXPLICIT_ZETHERION_ENV_FILE" >&2
            exit 1
        fi
    else
        ENV_FILE_PATH="$EXPLICIT_ZETHERION_ENV_FILE"
    fi
elif [ -f "$DEFAULT_ZETHERION_ENV_FILE" ]; then
    ENV_FILE_PATH="$DEFAULT_ZETHERION_ENV_FILE"
fi

HOST_ENV_FILE_PATH=""
CONTAINER_ENV_FILE_PATH=""
if [ -n "$ENV_FILE_PATH" ]; then
    case "$ENV_FILE_PATH" in
        /*) ;;
        *) ENV_FILE_PATH="$REPO_DIR/$ENV_FILE_PATH" ;;
    esac
    HOST_ENV_FILE_PATH="$(map_repo_path_to_host "$ENV_FILE_PATH")"
    CONTAINER_ENV_FILE_PATH="$(map_repo_path_to_container "$ENV_FILE_PATH")"
fi

if [ -n "$HOST_ENV_FILE_PATH" ]; then
    ENV_ARGS+=("--env-file" "$HOST_ENV_FILE_PATH")
fi

if [ -d "$SIBLING_CGS_ROOT_DEFAULT" ] && [ "$SIBLING_CGS_ROOT_DEFAULT" != "$REPO_DIR" ]; then
    ENV_ARGS+=(
        "-e" "CGS_WORKSPACE_ROOT=$SIBLING_CGS_MOUNT_TARGET"
        "-e" "CGS_DOCKER_HOST_ROOT=$SIBLING_CGS_ROOT_DEFAULT"
    )
fi

collect_env_args

RUN_ARGS=(
    docker run --rm -i
    -v "$HOST_WORKSPACE_ROOT:$WORKSPACE_MOUNT_TARGET"
    -w "$WORKSPACE_MOUNT_TARGET"
    --add-host=host.docker.internal:host-gateway
)

if [ "$HOST_WORKSPACE_ROOT" != "$WORKSPACE_MOUNT_TARGET" ]; then
    # Preserve direct access to the host workspace path so generated E2E paths
    # such as /mnt/c/.../.artifacts/... remain visible to nested tool containers.
    RUN_ARGS+=(-v "$HOST_WORKSPACE_ROOT:$HOST_WORKSPACE_ROOT")
fi

if [ "$HOST_WORKSPACE_ROOT" = "$REPO_DIR" ] && [ "$REPO_DIR" != "$WORKSPACE_MOUNT_TARGET" ]; then
    RUN_ARGS+=(-v "$REPO_DIR:$REPO_DIR")
fi

if [ -d "$SIBLING_CGS_ROOT_DEFAULT" ] \
    && [ "$SIBLING_CGS_ROOT_DEFAULT" != "$REPO_DIR" ] \
    && [ "${SIBLING_CGS_ROOT_DEFAULT#"$HOST_WORKSPACE_ROOT"/}" = "$SIBLING_CGS_ROOT_DEFAULT" ]; then
    RUN_ARGS+=(-v "$SIBLING_CGS_ROOT_DEFAULT:$SIBLING_CGS_MOUNT_TARGET")
fi

if [ -n "$HOST_ENV_FILE_PATH" ] && [ "${HOST_ENV_FILE_PATH#"$HOST_WORKSPACE_ROOT"/}" = "$HOST_ENV_FILE_PATH" ]; then
    RUN_ARGS+=(-v "$HOST_ENV_FILE_PATH:$CONTAINER_ENV_FILE_PATH:ro")
fi

if [ -S /var/run/docker.sock ]; then
    RUN_ARGS+=(-v /var/run/docker.sock:/var/run/docker.sock)
fi

if [ -t 0 ] && [ -t 1 ]; then
    RUN_ARGS+=(-t)
fi

RUN_ARGS+=("${ENV_ARGS[@]}" "$IMAGE_TAG" python "$@")

exec "${RUN_ARGS[@]}"
