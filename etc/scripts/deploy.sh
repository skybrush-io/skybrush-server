#!/bin/bash
#
# Builds single-file distributions of the Flockwave server for Windows
# and Linux (64-bit) using Docker.

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."

cd ${REPO_ROOT}

# Remove all requirements.txt files, we don't use them, only pipenv
rm -f requirements*.txt

# Generate requirements.txt from pipenv. Caveats:
# - we cannot call the file requirements.txt because the Docker container would
#   attempt to install them first before we get the chance to upgrade to pip 10
poetry export -f requirements.txt -o requirements-main.txt

if [ x$1 = xlinux ]; then
    GENERATE_LINUX=1
elif [ x$1 = xwin -o x$1 = xwindows ]; then
    GENERATE_WINDOWS=1
else
    GENERATE_LINUX=1
    GENERATE_WINDOWS=1
fi

# Generate the bundle for Linux
if [ x$GENERATE_LINUX = x1 ]; then
    rm -rf dist/linux
    docker run --rm -v "$(pwd):/src/" cdrx/pyinstaller-linux:python3 \
        "rm -rf /tmp/.wine-0 && apt-get update && apt-get remove -y python-pip && apt-get install -y curl git netbase && curl https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && python3 /tmp/get-pip.py && pip install --upgrade pip && pip install -r requirements-main.txt && pyinstaller --clean -y --dist ./dist/linux --workpath /tmp pyinstaller.spec && chown -R --reference=. ./dist/linux"
fi

# Generate the bundle for Windows
if [ x$GENERATE_WINDOWS = x1 ]; then
    rm -rf dist/windows
    docker run --rm -v "$(pwd):/src/" cdrx/pyinstaller-windows:Python3 \
        "rm -rf /tmp/.wine-0 && pip install -r requirements-main.txt && pyinstaller --clean -y --dist ./dist/windows --workpath /tmp pyinstaller.spec && chown -R --reference=. ./dist/windows"
fi

