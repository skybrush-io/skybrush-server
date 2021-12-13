#!/bin/bash
#
# Builds a single-dir distribution obfuscated with PyArmor, suitable for
# a standalone installation.

BUILD_DIR="./build/pyarmor"
OUTPUT_DIR="./dist/pyarmor"
TMP_DIR="./tmp"
OBFUSCATED_PACKAGES="aiocflib flockwave skybrush"
TARBALL_NAME="skybrush-server"
VENV_DIR="${VENV_DIR:-.venv}"

###############################################################################

set -e

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."
STANDALONE=0
KEEP_STAGING_FOLDER=0
BUILD_TARBALL=1
FROM_WHEELHOUSE=

while [ "x$1" != x ]; do
  if [ "x$1" = "x--standalone" ]; then
    STANDALONE=1
    shift
  elif [ "x$1" = "x--keep-staging" ]; then
    KEEP_STAGING_FOLDER=1
    shift
  elif [ "x$1" = "x--no-tarball" ]; then
    BUILD_TARBALL=0
    shift
  elif [ "x$1" = "x--wheelhouse" ]; then
    FROM_WHEELHOUSE="$2"
    shift
    shift
  else
    VENV_DIR="$1"
    shift
  fi
done

cd "${REPO_ROOT}"

# Use GNU tar if available; useful on macOS
if [ "$(uname)" = "Darwin" ]; then
  TAR=gtar
else
  TAR=tar
fi

TAR_PATH=`which $TAR 2>/dev/null || true`

if [ "x${TAR_PATH}" = x ]; then
  echo "$TAR must be installed on this platform before using this script"
  exit 1
fi

# Extract the name of the project and the version number from pyproject.toml
PROJECT_NAME=`cat pyproject.toml|grep ^name|head -1|cut -d '"' -f 2`
VERSION=`cat pyproject.toml|grep ^version|head -1|cut -d '"' -f 2`

POETRY=`which poetry 2>/dev/null || true`

if [ "x${FROM_WHEELHOUSE}" != x ]; then
  # Caller has prepared all the wheels in a folder, use those
  ls "${FROM_WHEELHOUSE}"/*.whl >requirements-main.txt
  ls "${FROM_WHEELHOUSE}"/*.tar* >>requirements-main.txt
elif [ "x${POETRY}" != x ]; then
  # Remove all requirements.txt files, we don't use them, only poetry
  rm -f requirements*.txt

  # Generate requirements.txt from poetry. We use requirements-main.txt for sake of
  # compatibility with building this in a pyinstaller Docker container where we
  # cannot use requirements.txt
  "${POETRY}" export -f requirements.txt --without-hashes --with-credentials | \
      grep -v '^pyobjc' \
      >requirements-main.txt
  trap "rm -f requirements-main.txt" EXIT

  # Build the Skybrush wheel and append it to the requirements
  rm -rf dist/"${PROJECT_NAME}"*.whl
  "${POETRY}" build -f wheel
  ls dist/*.whl >>requirements-main.txt
else
  echo "Poetry not installed; we assume that the requirements are already prepared in requirements-main.txt"
fi

# Create virtual environment if it doesn't exist yet
if [ ! -d "${VENV_DIR}" ]; then
  python3 -m venv "${VENV_DIR}"
fi
PIP="${VENV_DIR}/bin/pip"
PYARMOR="${VENV_DIR}/bin/pyarmor"
PYINSTALLER="${VENV_DIR}/bin/pyinstaller"
PYTHON="${VENV_DIR}/bin/python"

# Create build folder
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/bin"
mkdir -p "${BUILD_DIR}/lib"

# Install dependencies
"${PIP}" install -U pip wheel "pyarmor>=7.2.2" "pyinstaller>=4.7"

if [ "x$FROM_WHEELHOUSE" != x ]; then
  "${PIP}" install --no-deps --no-index --find-links="${FROM_WHEELHOUSE}"/ -r requirements-main.txt -t "${BUILD_DIR}/lib"
else
  "${PIP}" install -r requirements-main.txt -t "${BUILD_DIR}/lib"
fi

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
  PYARMOR_ARGS="--advanced 2" etc/scripts/_apply-pyarmor-on-venv.sh "${PYARMOR}" "${BUILD_DIR}/lib" "${BUILD_DIR}/obf" --keep

  # Call PyInstaller to produce an unobfuscated distribution first
  # TODO(ntamas): BUILD_DIR is hardcoded into pyinstaller.spec
  # Apparently we need to pass PYTHONPATH earlier because pkg_resources does not
  # find stuff in it if we let PyInstaller extend the PATH later
  PYTHONPATH="${BUILD_DIR}/lib" "${PYINSTALLER}" --clean -y --dist "${BUILD_DIR}/dist" etc/deployment/pyinstaller/pyinstaller.spec

  # Replace the unobfuscated libraries with the obfuscated ones in the PyInstaller
  # distribution
  "${PYTHON}" etc/deployment/pyarmor/repack.py -p "${BUILD_DIR}/obf" "${BUILD_DIR}/dist/skybrushd"
  mv skybrushd_obf ${BUILD_DIR}/bin/skybrushd

  # Clean up after ourselves
  rm -rf skybrushd_extracted
  rm -rf "${BUILD_DIR}/dist"
  rm -rf "${BUILD_DIR}/lib"
  rm -rf "${BUILD_DIR}/obf"

  # Okay, so in the build dir we now have doc/* with the license files and bin/
  # with the obfuscated bundle. Let's rearrange stuff a bit so we have a place for
  # the config file and the license file
  rm -rf "${BUILD_DIR}/staging"
  mkdir -p "${BUILD_DIR}/staging/bin"
  mv "${BUILD_DIR}/bin" "${BUILD_DIR}/staging/lib"
  mv "${BUILD_DIR}/doc" "${BUILD_DIR}/staging/doc"

  # TODO(ntamas): create separate bundles for indoor and outdoor
  cp etc/deployment/configs/skybrush-outdoor.jsonc "${BUILD_DIR}/staging/skybrush.jsonc"
  cp etc/deployment/configs/skybrush-outdoor.jsonc "${BUILD_DIR}/staging/skybrush-config-template.jsonc"
  cp etc/deployment/linux/skybrushd "${BUILD_DIR}/staging/bin/skybrushd"
  chmod a+x "${BUILD_DIR}/staging/bin/skybrushd"
else
  # Separate obfuscated Python modules; typically for a Raspberry Pi

  # Invoke obfuscation script on virtualenv
  etc/scripts/_apply-pyarmor-on-venv.sh "${PYARMOR}" "${BUILD_DIR}/lib" "${BUILD_DIR}/obf"

  # Create a launcher script
  cp etc/deployment/pyarmor/skybrushd "${BUILD_DIR}/bin"

  # Move everything to the staging dir
  mkdir -p "${BUILD_DIR}/staging"
  mv "${BUILD_DIR}/bin" "${BUILD_DIR}/lib" "${BUILD_DIR}/staging"
fi

# Add version file
echo "${VERSION}" >"${BUILD_DIR}/staging/VERSION"

# Create a tarball
if [ "x$BUILD_TARBALL" = x1 ]; then
  TARBALL_STEM="${TARBALL_NAME}-${VERSION}"
  rm -rf "${TMP_DIR}/${TARBALL_STEM}"
  mkdir -p "${TMP_DIR}/${TARBALL_STEM}"
  mkdir -p "${OUTPUT_DIR}"
  mv ${BUILD_DIR}/staging/* "${TMP_DIR}/${TARBALL_STEM}"
  $TAR -C "${TMP_DIR}" --owner=0 --group=0 --exclude "__pycache__" -czf "${OUTPUT_DIR}/${TARBALL_STEM}.tar.gz" "${TARBALL_STEM}/"
  rm -rf "${TMP_DIR}/${TARBALL_STEM}"
fi

# Remove staging folder
if [ "x$KEEP_STAGING_FOLDER" = x0 ]; then
  rm -rf "${BUILD_DIR}/staging"
fi

echo ""
echo "------------------------------------------------------------------------"
echo ""
if [ "x$BUILD_TARBALL" = x1 ]; then
  echo "Obfuscated bundle created successfully in ${OUTPUT_DIR}/${TARBALL_STEM}.tar.gz"
fi
if [ "x$KEEP_STAGING_FOLDER" = x1 ]; then
  echo "Staging folder was kept at ${BUILD_DIR}/staging"
fi
