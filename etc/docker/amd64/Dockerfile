# syntax = docker/dockerfile:1.0-experimental
FROM python:3.7-alpine

# Update all the things
RUN apk update && apk upgrade && \
    apk add --no-cache bash gcc git linux-headers musl-dev openssh-client

# Download public key for git.collmot.com
RUN mkdir -p -m 0600 ~/.ssh && ssh-keyscan git.collmot.com >>~/.ssh/known_hosts

# Install pipenv because we use it for dependency management
RUN pip install --no-cache-dir pipenv

# Switch working directory
WORKDIR /app

# Copy the application itself
COPY Pipfile Pipfile.lock .
COPY flockwave ./flockwave/
COPY bin ./bin/
COPY etc/docker ./etc/docker/
COPY etc/scripts ./etc/scripts/

# Run the build script
RUN --mount=type=ssh pipenv install

# Start the server
ENV PIP_NO_CACHE_DIR=1
CMD ["pipenv", "run", "bin/flockwaved"]

