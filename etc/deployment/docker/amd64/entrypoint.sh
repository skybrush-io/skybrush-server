#!/bin/sh
cd /app

. .venv/bin/activate

MODULE="$1"

if [ "x${MODULE}" = xserver ]; then
	shift
elif [ "x${MODULE}" = xgateway ]; then
	shift
else
	MODULE=server
fi

cd /data
PYTHONPATH=/app/src python -m flockwave.${MODULE}.launcher "$@"
