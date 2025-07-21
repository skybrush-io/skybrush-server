#!/bin/bash
#
# Builds the skybrush-server Docker image.

IMAGE_NAME=skybrush-server

SCRIPT_ROOT=$(dirname $0)
REPO_ROOT="${SCRIPT_ROOT}/../.."

set -e

cd ${REPO_ROOT}

# Remove all requirements.txt files, we don't use them, only poetry
rm -f requirements*.txt

# Generate requirements.txt from poetry
poetry export -f requirements.txt -o requirements-main.txt --without-hashes --with-credentials
trap "rm -f requirements-main.txt" EXIT

# Build the Docker image
docker build --platform linux/amd64 -t docker.collmot.com/${IMAGE_NAME}:latest -f etc/deployment/docker/amd64/Dockerfile .
echo "Successfully built Docker image."

# If we are at an exact tag, also tag the image
GIT_TAG=$(git describe --exact-match --tags 2>/dev/null || echo "")
if [ "x$GIT_TAG" != x ]; then
  docker tag docker.collmot.com/${IMAGE_NAME}:latest docker.collmot.com/${IMAGE_NAME}:${GIT_TAG}
  echo "Image tagged as $GIT_TAG."
fi
