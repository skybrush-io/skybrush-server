#!/bin/bash
#
# Builds single-file distributions of the Flockwave server for Windows,
# Linux (amd64) and macOS (hopefully both Intel and ARM).

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."

set -e

cd "${REPO_ROOT}"

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

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
    # Use Python 3.9 virtualenv because batonogov/pyinstaller-linux bundles
    # Python 3.9.9
    VENV_DIR="/root/.pyenv/versions/3.9.9"

    # Generate requirements.txt from poetry. Caveats:
    # - we cannot call the file requirements.txt because the Docker container would
    #   attempt to install them first before we get the chance to upgrade to pip 10
    poetry export -f requirements.txt --without-hashes --with-credentials | \
        grep -v '^pyobjc' >requirements-main.txt
    trap "rm -f requirements-main.txt" EXIT

    # Build the Skybrush wheel and append it to the requirements
    rm -rf dist/*.whl
    poetry build
    ls dist/*.whl >>requirements-main.txt

    rm -rf dist/linux
    docker run --rm \
        --platform linux/amd64 \
        -v "$(pwd):/src/" \
        -v "${HOME}/.pyarmor:/root/.pyarmor/" \
        -e VENV_DIR="${VENV_DIR}" \
        --entrypoint /bin/bash \
        batonogov/pyinstaller-linux:python_3.9 \
        -c "rm -rf /tmp/.wine-0 && apt-get update && apt-get remove -y python-pip && apt-get install -y curl git netbase && curl https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && ${VENV_DIR}/bin/python /tmp/get-pip.py && etc/scripts/build-pyarmored-dist.sh --standalone ${VENV_DIR}"

    rm -f requirements.txt
fi

# Generate the bundle for macOS
if [ x$GENERATE_MACOS = x1 ]; then
    rm -rf dist/mac
    etc/deployment/mac/build.sh
fi

# Generate the bundle for Windows
if [ x$GENERATE_WINDOWS = x1 ]; then
    rm -rf dist/windows
    etc/deployment/nsis/build.sh
fi
