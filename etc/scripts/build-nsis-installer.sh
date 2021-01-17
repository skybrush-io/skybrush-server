#!/bin/bash
#
# Builds an NSIS installer for the server application

BUILD_DIR="./build/nsis"
WHEEL_DIR="./build/wheels"
OUTPUT_DIR="./dist/windows"
OBFUSCATE=1
PYTHON_VERSION=3.7.9

###############################################################################

if [ "x$1" = "x--no-obfuscate" ]; then
    echo "WARNING: creating unobfuscated build! Press ^C now if you don't want this."
    sleep 5
    OBFUSCATE=0
fi

set -e

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."

cd "${REPO_ROOT}"

# Extract the name of the project and the version number from pyproject.toml
PROJECT_NAME=`cat pyproject.toml|grep ^name|head -1|cut -d '"' -f 2`
VERSION=`cat pyproject.toml|grep ^version|head -1|cut -d '"' -f 2`

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Build the Skybrush wheel first
rm -rf dist/"${PROJECT_NAME}"*.whl
poetry build -f wheel

# Generate requirements.txt files. We assume Python 3.7.9 because some packages
# do not provide wheels for 3.8 yet
poetry export -f requirements.txt -o requirements.txt --without-hashes --with-credentials
.venv/bin/python etc/deployment/nsis/collect-win32-deps.py ${PYTHON_VERSION}
ls dist/`echo ${PROJECT_NAME} | sed -e 's/-/_/g'`*.whl >>requirements-win32-wheels.txt
trap "rm -f requirements.txt requirements-win32-*.txt installer.cfg" EXIT

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

INSTALLER_NAME="Skybrush Server Setup ${VERSION}.exe"
if [ $OBFUSCATE -le 0 ]; then
  INSTALLER_NAME="Unobfuscated ${INSTALLER_NAME}"
fi

# Create installer.cfg for pynsist
# We temporarily copy skybrushd.py to the root because pynsist wants it to be
# there. We could use an alternative path, but then that would be used in the
# shortcut in the generated desktop shortcut, with slashes :(
cp etc/deployment/nsis/skybrushd.py skybrushd-win32.py
cat >installer.cfg <<EOF
[Application]
name=Skybrush Server
version=${VERSION}
publisher=CollMot Robotics
target=\$INSTDIR\\skybrushd.bat
parameters=
icon=assets/icons/win/skybrushd.ico
console=true

[Python]
version=${PYTHON_VERSION}
bitness=32

[Include]
local_wheels=${WHEEL_DIR}/*.whl
files=etc/blobs/win32/libusb-1.0.dll >\$INSTDIR\lib
    etc/deployment/nsis/skybrushd.bat >\$INSTDIR
    skybrushd-win32.py >\$INSTDIR
    etc/deployment/nsis/skybrush.jsonc >\$INSTDIR

[Build]
installer_name=../../dist/windows/${INSTALLER_NAME}
EOF

# Now clean the build dir and invoke pynsist, but don't run makensis just yet;
# we will need to obfuscate stuff before that
rm -rf "${BUILD_DIR}"
.venv/bin/python -m nsist installer.cfg --no-makensis

if [ $OBFUSCATE -gt 0 ]; then
  # Install the _exact_ Python version that we are going to use with
  # PyArmor. PyArmor absolutely needs a matching Python version when doing
  # cross-platform builds
  pyenv install $PYTHON_VERSION --skip-existing

  # Create a virtualenv with the given Python version and install pyarmor in it
  PYARMOR_VENV_NAME=pyarmor-$PYTHON_VERSION
  PYARMOR_VENV=`pyenv virtualenv-prefix ${PYARMOR_VENV_NAME} 2>/dev/null || true`
  if [ "x${PYARMOR_VENV}" = x ]; then
    pyenv virtualenv $PYTHON_VERSION $PYARMOR_VENV_NAME
    PYARMOR_VENV=`pyenv virtualenv-prefix ${PYARMOR_VENV_NAME}`
  fi
  if [ "x${PYARMOR_VENV}" = x ]; then
    echo "Could not create PyArmor virtualenv!"
    exit 1
  fi
  ${PYARMOR_VENV}/bin/pip install -U pyarmor

  # Obfuscate the source
  PYARMOR_PLATFORM=darwin.x86_64.0 TARGET_PLATFORM=windows.x86.0 etc/scripts/apply-pyarmor-on-venv.sh ${PYARMOR_VENV}/bin/pyarmor "${BUILD_DIR}/pkgs" "${BUILD_DIR}/obf"
fi

# Okay, call makensis now
makensis "${BUILD_DIR}"/installer.nsi

# Finally, remove the entry script that was put there only for pynsist's sake
rm skybrushd-win32.py

echo ""
echo "------------------------------------------------------------------------"

echo ""
echo "Installer created in dist/windows/${INSTALLER_NAME}"
if [ $OBFUSCATE -le 0 ]; then
  echo ""
  echo "WARNING: THIS BUILD IS UNOBFUSCATED! DO NOT DISTRIBUTE!"
fi

