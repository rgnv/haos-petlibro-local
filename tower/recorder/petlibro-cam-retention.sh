#!/bin/bash
# Petlibro camera clip retention: delete clips older than 30 days.
set -eu
ROOT="${PETLIBRO_CLIPS_DIR:?Set PETLIBRO_CLIPS_DIR}"
[ -d "$ROOT" ] || exit 1
[ -d "$ROOT" ] && [ ! -L "$ROOT" ] || exit 1
# Avoid treating path characters as regular-expression syntax.
cd -- "$ROOT"
ROOT=.
# Only direct date directories and recorder-owned files; never follow symlinks.
find "$ROOT" -xdev -regextype posix-extended -mindepth 2 -maxdepth 2 -type f \
  -regex "$ROOT/[0-9]{4}-[0-9]{2}-[0-9]{2}/tower_(motion|feed)_[^/]+\\.mp4" \
  -mtime +30 -delete
find "$ROOT" -xdev -regextype posix-extended -mindepth 2 -maxdepth 2 -type f \
  -regex "$ROOT/[0-9]{4}-[0-9]{2}-[0-9]{2}/tower_(motion|feed)_[^/]+\\.mp4\\.part" \
  -mtime +1 -delete
find "$ROOT" -xdev -regextype posix-extended -mindepth 1 -maxdepth 1 -type d \
  -regex "$ROOT/[0-9]{4}-[0-9]{2}-[0-9]{2}" -empty -delete
