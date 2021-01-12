#!/bin/bash

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../../.."

cd "${REPO_ROOT}"/etc/scripts
bash ./build-nsis-installer.sh

