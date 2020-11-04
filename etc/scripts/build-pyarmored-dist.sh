#!/bin/bash
#
# Builds a single-dir distribution obfuscated with PyArmor, suitable for
# a standalone installation.

PROJECT_NAME="flockwave-server"
OUTPUT_DIR="./dist/pyarmor"
TMP_DIR="./tmp"
OBFUSCATED_PACKAGES="aiocflib flockwave skybrush"

###############################################################################

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."

cd "${REPO_ROOT}"

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Build the Skybrush source tarball first
rm -rf dist/"${PROJECT_NAME}"*.tar.gz
poetry build -f sdist

# Generate requirements.txt from poetry
poetry export -f requirements.txt --without-hashes --with-credentials >requirements.txt
ls dist/${PROJECT_NAME}*.tar.gz >>requirements.txt
trap "rm -f requirements.txt" EXIT

# Create virtual environment if it doesn't exist yet
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}/bin"
mkdir -p "${OUTPUT_DIR}/doc"
mkdir -p "${OUTPUT_DIR}/lib"
.venv/bin/pip install -U pip wheel
.venv/bin/pip install -r requirements.txt -t "${OUTPUT_DIR}/lib"

# Remove executables of dependencies; they are not needed
rm -rf "${OUTPUT_DIR}/lib/bin"

# Obfuscate the packages that we need to
rm -rf "${OUTPUT_DIR}/lib-obfuscated"
for PACKAGE in ${OBFUSCATED_PACKAGES}; do
  mkdir -p "${OUTPUT_DIR}/lib-obfuscated/${PACKAGE}"
  poetry run pyarmor obfuscate -n --advanced 2 --recursive --output "${OUTPUT_DIR}/lib-obfuscated/${PACKAGE}" "${OUTPUT_DIR}/lib/${PACKAGE}/__init__.py"
done

# Obfuscate the launcher script as well
cp "src/flockwave/server/__main__.py" "${OUTPUT_DIR}/skybrushd.py"
poetry run pyarmor obfuscate --advanced 2 --exact --output "${OUTPUT_DIR}/lib-obfuscated" "${OUTPUT_DIR}/skybrushd.py"
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
  grep -riL pyarmor ${OBFUSCATED_PACKAGES} | grep "\.py$" 
)
NOT_OBFUSCATED_COUNT=$(echo "$NOT_OBFUSCATED_FILES" | grep -v -e '^[[:space:]]*$' | wc -l | sed -e 's/^ *//g')
if [ $NOT_OBFUSCATED_COUNT -gt 0 ]; then
  echo ""
  echo "WARNING: the following $NOT_OBFUSCATED_COUNT file(s) are not obfuscated:"
  echo ""
  echo "$NOT_OBFUSCATED_FILES"
  exit 1
fi

echo ""
echo "------------------------------------------------------------------------"
echo ""
echo "Obfuscated bundle created successfully in ${OUTPUT_DIR}"

