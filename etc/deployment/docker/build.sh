#!/bin/bash
#
# Builds the ${IMAGE_NAME} Docker image.

IMAGE_NAME=skybrush-server

SCRIPT_ROOT=`dirname $0`
REPO_ROOT="${SCRIPT_ROOT}/../../.."

set -e

cd ${REPO_ROOT}

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Generate requirements.txt from pipenv. We use requirements-main.txt for sake
# of consistency with deploy.sh
poetry export -f requirements.txt --without-hashes --with-credentials | \
    grep -v '^pyobjc' \
    >requirements-main.txt
trap "rm -f requirements-main.txt" EXIT

# Build the Docker image
DOCKER_BUILDKIT=1 docker build \
    --platform linux/amd64 \
    -t docker.collmot.com/${IMAGE_NAME}:latest \
    -f etc/deployment/docker/amd64/Dockerfile \
    .
echo "Successfully built Docker image."

# If we are at an exact tag, also tag the image
GIT_TAG=`git describe --exact-match --tags 2>/dev/null || echo ""`
if [ "x$GIT_TAG" != x ]; then
    docker tag docker.collmot.com/${IMAGE_NAME}:latest docker.collmot.com/${IMAGE_NAME}:${GIT_TAG}
    echo "Image tagged as $GIT_TAG."
fi
