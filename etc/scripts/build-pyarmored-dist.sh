#!/bin/bash
#
# Builds a single-dir distribution obfuscated with PyArmor, suitable for
# a standalone installation.

OUTPUT_DIR="./dist/pyarmor"
TMP_DIR="./tmp"
OBFUSCATED_PACKAGES="aiocflib flockwave skybrush"

###############################################################################

set -e

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."
PYARMOR_ARGS="--with-license outer --advanced 2"

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
rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}/bin"
mkdir -p "${OUTPUT_DIR}/doc"
mkdir -p "${OUTPUT_DIR}/lib"

# Install dependencies
.venv/bin/pip install -U pip wheel pyarmor
.venv/bin/pip install -r requirements.txt -t "${OUTPUT_DIR}/lib"

# lxml is huge and we don't need it; pymavlink brings it in mistakenly as a
# dependency
rm -rf "${OUTPUT_DIR}/lib/lxml"
rm -rf "${OUTPUT_DIR}/lib/lxml*.dist-info"

# Remove executables of dependencies; they are not needed
rm -rf "${OUTPUT_DIR}/lib/bin"

# Obfuscate the packages that we need to
rm -rf "${OUTPUT_DIR}/lib-obfuscated"
for PACKAGE in ${OBFUSCATED_PACKAGES}; do
  mkdir -p "${OUTPUT_DIR}/lib-obfuscated/${PACKAGE}"
  .venv/bin/pyarmor obfuscate $PYARMOR_ARGS --no-runtime --recursive --output "${OUTPUT_DIR}/lib-obfuscated/${PACKAGE}" "${OUTPUT_DIR}/lib/${PACKAGE}/__init__.py"
done

# Obfuscate the launcher script as well
cp "src/flockwave/server/__main__.py" "${OUTPUT_DIR}/skybrushd.py"
.venv/bin/pyarmor obfuscate $PYARMOR_ARGS --exact --output "${OUTPUT_DIR}/lib-obfuscated" "${OUTPUT_DIR}/skybrushd.py"
rm "${OUTPUT_DIR}/skybrushd.py"

# Move the obfuscated scripts back to lib/ to overwrite the originals
(
  cd "${OUTPUT_DIR}/lib-obfuscated";
  for i in `find . -type f -name '*.py'`; do
	mv "$i" "../lib/$i"
  done
)
mv "${OUTPUT_DIR}"/lib-obfuscated/pytransform* "${OUTPUT_DIR}"/lib/
rm -rf "${OUTPUT_DIR}/lib-obfuscated"

# Raspberry Pi distributions need an extra symlink for pytransform.so
if [ -f "${OUTPUT_DIR}/lib/pytransform.cpython-37m-arm-linux-gnu.so" ]; then
  (
    cd "${OUTPUT_DIR}/lib";
    rm -f pytransform.cpython-37m-arm-linux-gnueabihf.so;
    ln -s pytransform.cpython-37m-arm-linux-gnu.so pytransform.cpython-37m-arm-linux-gnueabihf.so
  )
fi

# Create a launcher script
cp etc/deployment/pyarmor/skybrushd "${OUTPUT_DIR}/bin"

# Collect licenses to an appropriate folder
mkdir -p "${OUTPUT_DIR}/doc/licenses"
for FOLDER in `find "${OUTPUT_DIR}/lib" -name "*.dist-info" -type d`; do
  if [ -f "${FOLDER}/LICENSE" ]; then
    cp "${FOLDER}/LICENSE" "${OUTPUT_DIR}/doc/licenses/`basename "${FOLDER}" | cut -d '-' -f 1`.LICENSE"
  fi
done

# Validate that all files are obfuscated that need to be
NOT_OBFUSCATED_FILES=$(
  cd "${OUTPUT_DIR}/lib";
  grep -riL pyarmor ${OBFUSCATED_PACKAGES} | grep "\.py$"  || true
)
NOT_OBFUSCATED_COUNT=$(echo "$NOT_OBFUSCATED_FILES" | grep -v -e '^[[:space:]]*$' | wc -l | sed -e 's/^ *//g')
if [ $NOT_OBFUSCATED_COUNT -gt 0 ]; then
  echo ""
  echo "WARNING: the following $NOT_OBFUSCATED_COUNT file(s) are not obfuscated:"
  echo ""
  echo "$NOT_OBFUSCATED_FILES"
  exit 1
fi

# Create a tarball
TARBALL_STEM="${PROJECT_NAME}-${VERSION}"
rm -rf "${TMP_DIR}/${TARBALL_STEM}"
mkdir -p "${TMP_DIR}/${TARBALL_STEM}"
mv "${OUTPUT_DIR}"/* "${TMP_DIR}/${TARBALL_STEM}"
tar -C "${TMP_DIR}" --exclude "__pycache__" -cvvzf "${OUTPUT_DIR}/${TARBALL_STEM}.tar.gz" "${TARBALL_STEM}/"
rm -rf "${TMP_DIR}/${TARBALL_STEM}"

echo ""
echo "------------------------------------------------------------------------"
echo ""
echo "Obfuscated bundle created successfully in ${OUTPUT_DIR}/${TARBALL_STEM}.tar.gz"

