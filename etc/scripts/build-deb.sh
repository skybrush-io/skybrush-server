#!/bin/sh
# Builds a Debian package for armhf on a Raspberry Pi

PROJECT_NAME=flockwave-server

# Do not change anything below unless you know what you are doing

set -e

cd "`dirname $0`"
cd ../..
PKGROOT=`pwd`

TARGET_ARCH=armhf
BUILD_DIR=build/deb/${TARGET_ARCH}
REMOTE_BUILD_DIR=tmp/deb-build

if [ -f ${PKGROOT}/.env ]; then
  source ${PKGROOT}/.env
  # This will provide $DEB_BUILDER
fi

VERSION=`git describe 2>/dev/null || echo "0.0.0"`
PYVERSION=`PYTHONPATH=src python3 -c 'from flockwave.server.version import __version__; print(__version__)'`

rm -rf ${BUILD_DIR}
rm -rf dist/${PROJECT_NAME}-*.tar.gz

mkdir -p ${BUILD_DIR}
python setup.py sdist
tar -xvvzf dist/${PROJECT_NAME}-*.tar.gz -C build/deb/${TARGET_ARCH}

ssh ${DEB_BUILDER} "rm -rf ${REMOTE_BUILD_DIR} && mkdir -p ${REMOTE_BUILD_DIR}"
scp -r ${BUILD_DIR}/ ${DEB_BUILDER}:${REMOTE_BUILD_DIR}
scp -r debian/ ${DEB_BUILDER}:${REMOTE_BUILD_DIR}/${TARGET_ARCH}/${PROJECT_NAME}-*/
# TODO(ntamas): check whether there is a .netrc file on the build machine
LANG=C ssh -t ${DEB_BUILDER} \
	"cd ${REMOTE_BUILD_DIR}/${TARGET_ARCH}/${PROJECT_NAME}-*/ && chmod +x debian/rules && debuild -us -uc"
scp "${DEB_BUILDER}:${REMOTE_BUILD_DIR}/${TARGET_ARCH}/${PROJECT_NAME}_${PYVERSION}_armhf.deb" dist/
