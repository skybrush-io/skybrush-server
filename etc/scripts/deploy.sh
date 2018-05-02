#!/bin/bash
#
# Builds single-file distributions of the Flockwave server for Windows
# and Linux (64-bit) using Docker.

SCRIPT_ROOT=`dirname $0`
FLOCKWAVE_ROOT="${SCRIPT_ROOT}/../.."

cd ${FLOCKWAVE_ROOT}

# Remove all requirements.txt files, we don't use them, only pipenv
rm -f requirements*.txt

# Generate requirements.txt from pipenv. Caveats:
# - enum34 has to be excluded even if pipenv says that it's needed -- enum-compat
#   will bring it in if it is really needed
# - we cannot call the file requirements.txt because the Docker container would
#   attempt to install them first before we get the chance to upgrade to pip 10
pipenv lock -r | grep -v enum34 >requirements-main.txt
pipenv lock -r -d >requirements-dev.txt

# Generate the bundle for Linux
docker run --rm -v "$(pwd):/src/" cdrx/pyinstaller-linux \
        "rm -rf /tmp/.wine-0 && apt-get remove -y python3-pip python-pip && apt-get install -y curl git && curl https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && python3 /tmp/get-pip.py && pip install --upgrade pip && pip install -r requirements-main.txt && pyinstaller --clean -y --dist ./dist/linux --workpath /tmp flockwaved.spec && chown -R --reference=. ./dist/linux"

# Generate the bundle for Windows
docker run --rm -v "$(pwd):/src/" cdrx/pyinstaller-windows \
        "rm -rf /tmp/.wine-0 && pip install -r requirements-main.txt && pyinstaller --clean -y --dist ./dist/windows --workpath /tmp flockwaved.spec && chown -R --reference=. ./dist/windows"

