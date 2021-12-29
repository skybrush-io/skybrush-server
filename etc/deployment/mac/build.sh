#!/bin/bash
#
# Builds a macOS installer package for the server application
#
# Typically you don't need to call this; call `etc/scripts/deploy.sh mac`
# instead.

BUILD_DIR="./build/mac"
WHEEL_DIR="./build/wheels/mac"
OUTPUT_DIR="./dist/mac"
OBFUSCATE=1

PYTHON_VERSION=3.9.9
PYTHON_VERSION_SHORT=39

###############################################################################

# We assume that we are running on macOS
if [ ! -d /Applications ]; then
    echo "macOS version can only be built on macOS"
    exit 1
fi

if [ "x$1" = "x--no-obfuscate" ]; then
    echo "WARNING: creating unobfuscated build! Press ^C now if you don't want this."
    sleep 5
    OBFUSCATE=0
fi

set -e

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../../.."

cd "${REPO_ROOT}"

# Extract the name of the project and the version number from pyproject.toml
PROJECT_NAME=`cat pyproject.toml|grep ^name|head -1|cut -d '"' -f 2`
VERSION=`cat pyproject.toml|grep ^version|head -1|cut -d '"' -f 2`

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Build the Skybrush wheel first
rm -rf dist/"${PROJECT_NAME}"*.whl
poetry build -f wheel

# Generate requirements.txt files
poetry export -f requirements.txt --without-hashes --with-credentials \
    | grep -v lxml \
    >requirements.txt
ls dist/`echo ${PROJECT_NAME} | sed -e 's/-/_/g'`*.whl >>requirements.txt
trap "rm -f requirements.txt" EXIT

# PyNaCl needs setuptools, wheel and cffi as a build dependency. We can remove
# this once PyNaCl starts publishing universal wheels for macOS.
#
# pycparser is needed by cffi; we can remove pycparser once cffi starts publishing
# universal wheels for macOS.
cat <<EOF >>requirements.txt
setuptools>=46.0.0
cffi>=1.15.0
wheel>=0.37.0
pycparser>=2.21
EOF

# Set up environment variables to support macOS 10.13 at least. PyInstaller 4.7
# has wheels for macOS 10.13 so we cannot go any earlier than that (but it's
# okay). We cannot provide universal binaries yet because PyArmor does not
# provide universal dylibs so we use Intel.
export MACOSX_DEPLOYMENT_TARGET=10.13
export CFLAGS="-arch x86_64"
TARGET_PLATFORM="macosx_10_13_x86_64"

# Collect all wheels into a folder
rm -rf "${WHEEL_DIR}"
mkdir -p "${WHEEL_DIR}"
pip3 download -r requirements.txt \
    --platform ${TARGET_PLATFORM} --python-version ${PYTHON_VERSION_SHORT} --implementation cp --abi cp${PYTHON_VERSION_SHORT} \
    --prefer-binary --no-deps \
    --progress-bar pretty \
    -d "${WHEEL_DIR}"

# Now clean the build dir and install everything in a virtualenv in there
rm -rf "${BUILD_DIR}"
VENV_DIR="${BUILD_DIR}/venv"
python3 -m venv "${VENV_DIR}"

# TODO(ntamas): clean up unused MAVlink dialects somehow!

# At the moment, PyArmor does not work on Apple M1 (freezes when obfuscating)
# unless we set the PYARMOR_PLATFORM envvar. Note that we force it to x86_64 because
# we are running the next builder script in an x86_64 environment
if [ `uname -m` = "arm64" ]; then
    export PYARMOR_PLATFORM=darwin.x86_64.0
fi
export TARGET_PLATFORM=darwin.x86_64.11.py39
arch -x86_64 $(SHELL) etc/scripts/build-pyarmored-dist.sh --standalone --keep-staging --no-tarball --wheelhouse "${WHEEL_DIR}" "${VENV_DIR}"
etc/deployment/mac/build-installer.sh build/pyarmor/staging
rm -rf build/pyarmor/staging
