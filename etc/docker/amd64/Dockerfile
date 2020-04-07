# syntax = docker/dockerfile:1.0-experimental
FROM python:3.7-alpine AS builder

# Specify the argument that we use to pass in the personal access token used to
# check out stuff from our private git server
ARG GIT_ACCESS_TOKEN_USERNAME="nothing"
ARG GIT_ACCESS_TOKEN_PASSWORD="nothin"

# Update all the things
RUN apk update && apk upgrade && \
    apk add --no-cache bash gcc git libxslt-dev \
	    linux-headers musl-dev openssh-client

# Download public key for git.collmot.com
RUN mkdir -p -m 0600 ~/.ssh && ssh-keyscan git.collmot.com >>~/.ssh/known_hosts

# Switch working directory
WORKDIR /app

# Copy the application itself
COPY requirements.txt ./
COPY src/flockwave ./src/flockwave/
COPY bin ./bin/

# Create a virtual environment
RUN python -m venv .venv

# Create netrc file for credentials
RUN printf "machine git.collmot.com\n\
    login ${GIT_ACCESS_TOKEN_USERNAME}\n\
	password ${GIT_ACCESS_TOKEN_PASSWORD}\n" \
	>> /root/.netrc
RUN chmod 600 /root/.netrc

# Temporarily remove rtmidi-related dependencies from the requirements because
# rtmidi compilation is broken in Alpine. (setup.py in python-rtmidi relies on
# ctypes.util.find_library, which is broken in Alpine).
RUN sed -i -e 's/midi,//' requirements.txt

# Hack /usr/include/assert.h to make the prototypes compatible with what
# mavnative expects
RUN sed -i 's/, int,/, unsigned int,/' /usr/include/assert.h

# Run the build script
ENV PIP_NO_CACHE_DIR=1
RUN .venv/bin/pip install -r requirements.txt

# Start the second stage where we don't add the stuff that we only need for
# compiling things
FROM python:3.7-alpine

# Update all the things
RUN apk update && apk upgrade && apk add --no-cache tini

# Switch working directory
WORKDIR /app

# Copy the application from the first stage
COPY --from=builder /app .

# Create /data folder to put application-specific data in
RUN mkdir /data

# Set up the entrypoint
COPY etc/docker/amd64/entrypoint.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/sbin/tini", "--", "entrypoint.sh"]

