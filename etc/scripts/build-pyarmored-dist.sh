#!/bin/bash
#
# Builds a single-dir distribution obfuscated with PyArmor, suitable for
# a standalone installation.

BUILD_DIR="./build/pyarmor"
OUTPUT_DIR="./dist/pyarmor"
TMP_DIR="./tmp"
OBFUSCATED_PACKAGES="aiocflib flockwave skybrush"
TARBALL_NAME="skybrush-server"

###############################################################################

set -e

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."
STANDALONE=0

if [ "x$1" = "x--standalone" ]; then
  STANDALONE=1
fi

cd "${REPO_ROOT}"

# Extract the name of the project and the version number from pyproject.toml
PROJECT_NAME=`cat pyproject.toml|grep ^name|head -1|cut -d '"' -f 2`
VERSION=`cat pyproject.toml|grep ^version|head -1|cut -d '"' -f 2`

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Build the Skybrush source tarball first
rm -rf dist/"${PROJECT_NAME}"*.tar.gz
poetry build -f sdist

# Generate requirements.txt from poetry
poetry export -f requirements.txt -o requirements.txt --without-hashes --with-credentials
ls dist/${PROJECT_NAME}*.tar.gz >>requirements.txt
trap "rm -f requirements.txt" EXIT

# Create virtual environment if it doesn't exist yet
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

# Create build folder
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/bin"
mkdir -p "${BUILD_DIR}/lib"

# Install dependencies
.venv/bin/pip install -U pip wheel "pyarmor>=6.6.0" pyinstaller
.venv/bin/pip install -r requirements.txt -t "${BUILD_DIR}/lib"

# lxml is huge and we don't need it; pymavlink brings it in mistakenly as a
# dependency
rm -rf "${BUILD_DIR}/lib/lxml"
rm -rf "${BUILD_DIR}/lib/lxml*.dist-info"

# Remove executables of dependencies; they are not needed
rm -rf "${BUILD_DIR}/lib/bin"

# Collect licenses to an appropriate folder
mkdir -p "${BUILD_DIR}/doc/licenses"
for FOLDER in `find "${BUILD_DIR}/lib" -name "*.dist-info" -type d`; do
  if [ -f "${FOLDER}/LICENSE" ]; then
    cp "${FOLDER}/LICENSE" "${BUILD_DIR}/doc/licenses/`basename "${FOLDER}" | cut -d '-' -f 1`.LICENSE"
  fi
done

if [ $STANDALONE = 1 ]; then
  # Standalone single-executable edition using PyInstaller

  # Invoke obfuscation script on virtualenv. Note that we need --advanced 2 here
  # because the PyInstaller repacking trick does not work with the "normal"
  # mode.
  PYARMOR_ARGS="--advanced 2" etc/scripts/apply-pyarmor-on-venv.sh .venv/bin/pyarmor "${BUILD_DIR}/lib" "${BUILD_DIR}/obf" --keep

  # Call PyInstaller to produce an unobfuscated distribution first
  # TODO(ntamas): BUILD_DIR is hardcoded into pyinstaller.spec
  .venv/bin/pyinstaller --clean -y --dist "${BUILD_DIR}/dist" etc/deployment/pyinstaller/pyinstaller.spec

  # Replace the unobfuscated libraries with the obfuscated ones in the PyInstaller
  # distribution
  .venv/bin/python etc/deployment/pyarmor/repack.py -p "${BUILD_DIR}/obf" "${BUILD_DIR}/dist/skybrushd"
  mv skybrushd_obf ${BUILD_DIR}/bin/skybrushd

  # Clean up after ourselves
  rm -rf skybrushd_extracted
  rm -rf "${BUILD_DIR}/dist"
  rm -rf "${BUILD_DIR}/lib"
  rm -rf "${BUILD_DIR}/obf"
else
  # Separate obfuscated Python modules; typically for a Raspberry Pi

  # Invoke obfuscation script on virtualenv
  etc/scripts/apply-pyarmor-on-venv.sh .venv/bin/pyarmor "${BUILD_DIR}/lib" "${BUILD_DIR}/obf"

  # Create a launcher script
  cp etc/deployment/pyarmor/skybrushd "${BUILD_DIR}/bin"
fi

# Create a tarball
TARBALL_STEM="${TARBALL_NAME}-${VERSION}"
rm -rf "${TMP_DIR}/${TARBALL_STEM}"
mkdir -p "${TMP_DIR}/${TARBALL_STEM}"
mkdir -p "${OUTPUT_DIR}"
mv "${BUILD_DIR}"/* "${TMP_DIR}/${TARBALL_STEM}"
tar -C "${TMP_DIR}" --exclude "__pycache__" -czf "${OUTPUT_DIR}/${TARBALL_STEM}.tar.gz" "${TARBALL_STEM}/"
rm -rf "${TMP_DIR}/${TARBALL_STEM}"

echo ""
echo "------------------------------------------------------------------------"
echo ""
echo "Obfuscated bundle created successfully in ${OUTPUT_DIR}/${TARBALL_STEM}.tar.gz"

