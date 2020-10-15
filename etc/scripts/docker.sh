#!/bin/bash
#
# Builds the skybrush-server Docker image.

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../.."

set -e

cd ${REPO_ROOT}

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Generate requirements.txt from pipenv. We use requirements-main.txt for sake
# of consistency with deploy.sh
poetry export -f requirements.txt -o requirements-main.txt --without-hashes --with-credentials
trap "rm -f requirements-main.txt" EXIT

# Build the Docker image
DOCKER_BUILDKIT=1 docker build -t docker.collmot.com/skybrush-server:latest -f etc/docker/amd64/Dockerfile .
echo "Successfully built Docker image."

# If we are at an exact tag, also tag the image
GIT_TAG=`git describe --exact-match --tags 2>/dev/null || echo ""`
if [ "x$GIT_TAG" != x ]; then
    docker tag docker.collmot.com/skybrush-server:latest docker.collmot.com/skybrush-server:${GIT_TAG}
    echo "Image tagged as $GIT_TAG."
fi
