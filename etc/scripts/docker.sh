#!/bin/bash
#
# Builds the skybrush-server Docker image.

IMAGE_NAME=skybrush-server

SCRIPT_ROOT=$(dirname $0)
REPO_ROOT="${SCRIPT_ROOT}/../.."

set -e

cd ${REPO_ROOT}

# Remove all requirements.txt files, we don't use them, only uv
rm -f requirements*.txt

# Generate requirements.txt from uv
uv export --format requirements.txt -o requirements-main.txt \
  --no-annotate --no-dev --no-editable --no-emit-project --no-hashes >/dev/null
trap "rm -f requirements-main.txt" EXIT

# Build the Docker image
docker build \
  --platform linux/amd64 \
  --secret id=NETRC_SECRET_ID,src=${HOME}/.netrc \
  -t docker.collmot.com/${IMAGE_NAME}:latest \
  -f etc/deployment/docker/amd64/Dockerfile \
  .
echo "Successfully built Docker image."

# If we are at an exact tag, also tag the image
GIT_TAG=$(git describe --exact-match --tags 2>/dev/null || echo "")
if [ "x$GIT_TAG" != x ]; then
  docker tag docker.collmot.com/${IMAGE_NAME}:latest docker.collmot.com/${IMAGE_NAME}:${GIT_TAG}
  echo "Image tagged as $GIT_TAG."
fi
