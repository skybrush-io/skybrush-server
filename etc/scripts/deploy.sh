#!/bin/bash
#
# Builds single-file distributions of the Flockwave server for Windows
# and Linux (amd64) using Docker.

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."

set -e

cd "${REPO_ROOT}"

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Generate requirements.txt from poetry. Caveats:
# - we cannot call the file requirements.txt because the Docker container would
#   attempt to install them first before we get the chance to upgrade to pip 10
poetry export -f requirements.txt -o requirements-main.txt --without-hashes --with-credentials
trap "rm -f requirements-main.txt" EXIT

# Build the Skybrush wheel and append it to the requirements
rm -rf dist/*.whl
poetry build
ls dist/*.whl >>requirements-main.txt

if [ x$1 = xlinux ]; then
    GENERATE_LINUX=1
elif [ x$1 = xwin -o x$1 = xwindows ]; then
    GENERATE_WINDOWS=1
elif [ x$1 = xmac -o x$1 = xmacos ]; then
    GENERATE_MACOS=1
else
    GENERATE_LINUX=1
    GENERATE_WINDOWS=1
    GENERATE_MACOS=1
fi

# Generate the bundle for Linux
if [ x$GENERATE_LINUX = x1 ]; then
    VENV_DIR="/root/.pyenv/versions/3.7.9"

    rm -rf dist/linux
    docker run --rm \
        -v "$(pwd):/src/" \
        -v "${HOME}/.pyarmor:/root/.pyarmor/" \
        -e VENV_DIR="${VENV_DIR}" \
        --entrypoint /bin/bash \
        toilal/pyinstaller-linux:python3-xenial \
        -c "rm -rf /tmp/.wine-0 && apt-get update && apt-get remove -y python-pip && apt-get install -y curl git netbase && curl https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && ${VENV_DIR}/bin/python /tmp/get-pip.py && etc/scripts/build-pyarmored-dist.sh --standalone ${VENV_DIR}"
fi

# Generate the bundle for macOS
if [ x$GENERATE_MACOS = x1 ]; then
    # We assume that we are running on macOS
    if [ ! -d /Applications ]; then
        echo "macOS version can only be built on macOS"
        exit 1
    fi

    VENV_DIR=".venv"

    rm -rf dist/macos
    poetry install
    etc/scripts/build-pyarmored-dist.sh --standalone --keep-staging --no-tarball ${VENV_DIR}
    etc/deployment/darwin-x64/build.sh build/pyarmor/staging 1.8.0
    rm -rf build/pyarmor/staging
fi

# Generate the bundle for Windows
if [ x$GENERATE_WINDOWS = x1 ]; then
    rm -rf dist/windows
    etc/deployment/nsis/build.sh
fi
