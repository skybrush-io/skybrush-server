# syntax = docker/dockerfile:1.0-experimental
FROM python:3.11-slim AS builder

# Switch working directory
WORKDIR /app

# Copy the requirements file first so we don't need to rebuild the image from
# scratch if the requirements don't change
COPY requirements-main.txt ./

# Create a virtual environment
RUN python -m venv .venv

# Run the build script
ENV PIP_NO_CACHE_DIR=1
RUN .venv/bin/pip install wheel
RUN \
    --mount=type=secret,id=NETRC_SECRET_ID,dst=/root/.netrc \
    --mount=source=etc/deployment/docker/pip.conf,dst=/etc/pip.conf,readonly \
    .venv/bin/pip install -r requirements-main.txt

# Now we can copy the application itself
COPY ./src/flockwave ./src/flockwave

# Clean up __pycache__ files
RUN find . | grep -E "(__pycache__|\.pyc$)" | xargs rm -rf

# Start the second stage where we don't add the stuff that we only need for
# compiling things
FROM python:3.11-slim

# Add tini as an init system
ENV TINI_VERSION v0.19.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /tini
RUN chmod +x /tini

# Switch working directory
WORKDIR /app

# Copy the application from the first stage
COPY --from=builder /app .

# Create /data folder to put application-specific data in
RUN mkdir /data

# Set up the entrypoint
COPY etc/deployment/docker/amd64/entrypoint.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/tini", "--", "entrypoint.sh"]

# Expose the default port
EXPOSE 5000
