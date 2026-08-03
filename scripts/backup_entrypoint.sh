#!/bin/sh
set -eu

mount_path="${RAILWAY_VOLUME_MOUNT_PATH:-/backups}"
output_dir="${BACKUP_OUTPUT_DIR:-$mount_path}"

case "$mount_path" in
    /*) ;;
    *)
        echo "Backup entrypoint failed: volume mount path must be absolute" >&2
        exit 1
        ;;
esac

case "$output_dir" in
    "$mount_path"|"$mount_path"/*) ;;
    *)
        echo "Backup entrypoint failed: output directory must remain inside the mounted volume" >&2
        exit 1
        ;;
esac

mkdir -p "$output_dir"
chown amthero:amthero "$mount_path" "$output_dir"
chmod 0700 "$mount_path" "$output_dir"

exec gosu amthero "$@"
