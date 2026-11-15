#!/bin/sh
set -eu
umask 077
python /app/docker/check_chrome_age.py
exec "$@"
