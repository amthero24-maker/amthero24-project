#!/bin/sh
set -eu

mount_path="${RAILWAY_VOLUME_MOUNT_PATH:-/backups}"
artifact="${RESTORE_ARTIFACT:-}"
source_database_url="${DATABASE_URL:-}"
target_database_url="${RESTORE_TARGET_DATABASE_URL:-}"

case "$mount_path" in
    /*) ;;
    *)
        echo "Restore certification failed: volume mount path must be absolute" >&2
        exit 1
        ;;
esac

case "$artifact" in
    "$mount_path"/*.dump.fernet) ;;
    *)
        echo "Restore certification failed: RESTORE_ARTIFACT must name an encrypted artifact inside the mounted volume" >&2
        exit 1
        ;;
esac

if [ ! -f "$artifact" ] || [ ! -f "$artifact.manifest.json" ]; then
    echo "Restore certification failed: encrypted artifact and matching manifest are required" >&2
    exit 1
fi

if [ "${RESTORE_ALLOWED:-false}" != "true" ]; then
    echo "Restore certification failed: RESTORE_ALLOWED=true is required" >&2
    exit 1
fi

if [ "${RESTORE_TARGET_CONFIRMATION:-}" != "ISOLATED_RESTORE_TARGET" ]; then
    echo "Restore certification failed: isolated target confirmation is required" >&2
    exit 1
fi

if [ -z "$source_database_url" ] || [ -z "$target_database_url" ]; then
    echo "Restore certification failed: source and isolated target database references are required" >&2
    exit 1
fi

export RESTORE_SOURCE_DATABASE_URL="$source_database_url"
export DATABASE_URL="$target_database_url"
unset RESTORE_TARGET_DATABASE_URL

exec python -m scripts.postgres_restore "$artifact" --confirm RESTORE_AMTHERO24
