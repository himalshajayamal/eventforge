#!/bin/sh
set -eu

umask 077
mkdir -p /backups

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="/backups/eventforge-${stamp}.dump"

pg_dump   -h db   -U "${POSTGRES_USER}"   -d "${POSTGRES_DB}"   -Fc   -f "${file}"

if [ ! -s "${file}" ]; then
  echo "backup_failed=empty_dump" >&2
  exit 1
fi

echo "backup_created=${file}"
