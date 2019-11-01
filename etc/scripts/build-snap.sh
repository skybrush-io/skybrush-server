#!/bin/sh
# Builds a snap for armhf on a Raspberry Pi

PROJECT_NAME=flockwave-server

# Do not change anything below unless you know what you are doing

cd "`dirname $0`"
cd ../..
PKGROOT=`pwd`

TARGET_ARCH=armhf
BUILD_DIR=build/snap/${TARGET_ARCH}
REMOTE_BUILD_DIR=tmp/snap-build/${PROJECT_NAME}

if [ -f ${PKGROOT}/.env ]; then
  source ${PKGROOT}/.env
  # This will provide $SNAP_BUILDER
fi

VERSION=`git describe 2>/dev/null || echo "0.0.0"`

rm -rf ${BUILD_DIR}
rm -rf dist/${PROJECT_NAME}-*.tar.gz

mkdir -p ${BUILD_DIR}/snap/
python setup.py sdist
tar -xvvzf dist/${PROJECT_NAME}-*.tar.gz -C build/snap/armhf --strip-components=1
cat snap/snapcraft.yaml | sed -e "s/^version: git.*/version: ${VERSION}/" >${BUILD_DIR}/snap/snapcraft.yaml

# Make sure not to delete ${REMOTE_BUILD_DIR}/parts/python37 etc as it takes
# a long time to rebuild
ssh ${SNAP_BUILDER} "rm -rf ${REMOTE_BUILD_DIR}/src && rm -rf ${REMOTE_BUILD_DIR}/*.snap && mkdir -p ${REMOTE_BUILD_DIR}"
scp -r ${BUILD_DIR}/ ${SNAP_BUILDER}:${REMOTE_BUILD_DIR}
ssh -t ${SNAP_BUILDER} "cd ${REMOTE_BUILD_DIR}/${TARGET_ARCH} && classic snapcraft"
