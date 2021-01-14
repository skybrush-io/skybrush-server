#!/bin/bash
#
# Script that takes an existing Python virtualenv with all dependencies installed,
# and obfuscates the main files with PyArmor
#
# Use the TARGET_PLATFORM envvar to define a different target platform

###############################################################################

OBFUSCATED_PACKAGES="aiocflib flockwave skybrush"

###############################################################################

set -e

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."
PYARMOR_ARGS="--with-license outer"

if [ "x$TARGET_PLATFORM" != x ]; then
    PYARMOR_ARGS="${PYARMOR_ARGS} --platform ${TARGET_PLATFORM}"
fi

if [ "x$3" = x ]; then
    echo "Usage: $0 path-to-pyarmor libdir workdir [--keep]"
	exit 1
fi

PYARMOR="$1"
LIBDIR="$2"
WORKDIR="$3"
shift
shift
shift

# Resolve relative paths to absolute
CWD="`pwd`"
cd "${LIBDIR}"
LIBDIR="`pwd`"
cd "${CWD}"
mkdir -p "${WORKDIR}"
cd "${WORKDIR}"
WORKDIR="`pwd`"
cd "${CWD}"

# Move to the repo root
cd "${REPO_ROOT}"

# Obfuscate the packages that we need to
rm -rf "${WORKDIR}"
for PACKAGE in ${OBFUSCATED_PACKAGES}; do
  mkdir -p "${WORKDIR}/${PACKAGE}"
  ${PYARMOR} obfuscate $PYARMOR_ARGS --no-runtime --recursive --output "${WORKDIR}/${PACKAGE}" "${LIBDIR}/${PACKAGE}/__init__.py"
  sed -i -e 's/^from \.pytransform/from pytransform/' "${WORKDIR}/${PACKAGE}/__init__.py"
done

# Obfuscate the launcher script as well
cp "src/flockwave/server/__main__.py" "${LIBDIR}/skybrushd.py"
${PYARMOR} obfuscate $PYARMOR_ARGS --exact --output "${WORKDIR}" "${LIBDIR}/skybrushd.py"
rm "${LIBDIR}/skybrushd.py"

# Raspberry Pi distributions need an extra symlink for pytransform.so
if [ -f "${WORKDIR}/pytransform.cpython-37m-arm-linux-gnu.so" ]; then
  (
    cd "${WORKDIR}";
    rm -f pytransform.cpython-37m-arm-linux-gnueabihf.so;
    ln -s pytransform.cpython-37m-arm-linux-gnu.so pytransform.cpython-37m-arm-linux-gnueabihf.so
  )
fi

# If the user asked to keep the original files, we can exit here
if [ "x$1" = "x--keep" ]; then
  exit 0
fi

# Move the obfuscated scripts back to lib/ to overwrite the originals
if [ -d "${WORKDIR}/pytransform" ]; then
    mv "${WORKDIR}"/pytransform "${LIBDIR}"
fi
(
  cd "${WORKDIR}";
  for i in `find . -type f -name '*.py'`; do
	mv "$i" "${LIBDIR}/$i"
  done
)
mv "${WORKDIR}"/pytransform* "${LIBDIR}" || true

rm -rf "${WORKDIR}"

# Validate that all files are obfuscated that need to be
NOT_OBFUSCATED_FILES=$(
  cd "${LIBDIR}";
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

