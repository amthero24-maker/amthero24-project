#!/bin/sh
set -eu

mount_path="${RAILWAY_VOLUME_MOUNT_PATH:-}"
output_dir="${BACKUP_OUTPUT_DIR:-/backups}"

if [ -z "$mount_path" ]; then
    echo '{"reason":"missing_mount_variable","status":"failed"}' >&2
    exit 1
fi

case "$mount_path" in
    /*) ;;
    *)
        echo '{"reason":"mount_not_absolute","status":"failed"}' >&2
        exit 1
        ;;
esac

case "$output_dir" in
    "$mount_path"|"$mount_path"/*) ;;
    *)
        echo '{"reason":"output_outside_mount","status":"failed"}' >&2
        exit 1
        ;;
esac

python -m scripts.verify_backup_volume \
    --mount-path "$mount_path" \
    --output-dir "$output_dir"

mkdir -p "$output_dir"
chown amthero:amthero "$mount_path" "$output_dir"
chmod 0700 "$mount_path" "$output_dir"

exec gosu amthero "$@"
