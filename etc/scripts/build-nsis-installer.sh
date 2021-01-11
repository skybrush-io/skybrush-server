#!/bin/bash
#
# Builds an NSIS installer for the server application

BUILD_DIR="./build/nsis"
WHEEL_DIR="./build/wheels"
OUTPUT_DIR="./dist/windows"
OBFUSCATED_PACKAGES="aiocflib flockwave skybrush"

###############################################################################

set -e

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."

cd "${REPO_ROOT}"

# Extract the name of the project and the version number from pyproject.toml
PROJECT_NAME=`cat pyproject.toml|grep ^name|head -1|cut -d '"' -f 2`
VERSION=`cat pyproject.toml|grep ^version|head -1|cut -d '"' -f 2`

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Build the Skybrush source tarball first
rm -rf dist/"${PROJECT_NAME}"*.tar.gz
poetry build -f sdist

# Generate requirements.txt files. We assume Python 3.7.9 because some packages
# do not provide wheels for 3.8 yet
poetry export -f requirements.txt -o requirements.txt --without-hashes --with-credentials
.venv/bin/python etc/scripts/collect-win32-deps.py
ls dist/`echo ${PROJECT_NAME} | sed -e 's/-/_/g'`*.whl >>requirements-win32-wheels.txt
trap "rm -f requirements.txt installer.cfg" EXIT

# Collect all wheels and stuff into a folder
rm -rf "${WHEEL_DIR}"
mkdir -p "${WHEEL_DIR}"
.venv/bin/pip download -r requirements-win32-wheels.txt \
	--platform win32 --python-version 37 --implementation cp --abi cp37m \
	--only-binary :all: --no-deps \
	--progress-bar pretty \
	-d "${WHEEL_DIR}"
DISABLE_MAVNATIVE=1 .venv/bin/pip wheel -r requirements-win32-source.txt \
	--no-binary :all: --no-deps \
	--progress-bar pretty \
	-w "${WHEEL_DIR}"

# Some packages do not have official Windows wheels, get the ones from Christoph Gohlke
rm "${WHEEL_DIR}"/crcmod*.whl
rm "${WHEEL_DIR}"/pyrsistent*.whl
cp etc/wheels/win32/*.whl "${WHEEL_DIR}"

# TODO(ntamas): clean up unused MAVlink dialects somehow!

# Create installer.cfg for pynsist
cat >installer.cfg <<EOF
[Application]
name=Skybrush Server
version=${VERSION}
publisher=CollMot Robotics
entry_point=flockwave.server.launcher:start
# TODO: icon
console=true

[Python]
version=3.7.9
bitness=32

[Include]
local_wheels=${WHEEL_DIR}/*.whl

[Build]
installer_name=../../dist/windows/Skybrush Server ${VERSION}.exe
EOF

# Now clean the build dir and invoke pynsist
rm -rf "${BUILD_DIR}"
.venv/bin/python -m nsist installer.cfg

echo ""
echo "------------------------------------------------------------------------"

