# Skybrush Backend Server Installation Guide

## Linux

1. Install `pipenv`.

2. I said install `pipenv`. :) Really. It manages a separate virtual environment
   for a given Python project so it has nearly zero dependencies on the system
   Python. You won't pollute the system Python with the dependencies of the
   Skybrush backend server and everyone will be happier.

3. Check out the source code of the backend server.

4. Run `pipenv install`.

5. Run `pipenv run start`.

If you want a single-file executable that includes a bundled Python interpreter
and all the dependencies, you can create one with PyInstaller:

1. Run `pipenv install --dev`.

2. Run `pipenv run pyinstaller skybrushd.spec`.

PyInstaller will create a single-file distribution in `dist/skybrushd`.

## Docker

You can build a Docker container on Linux with the following command line:

```sh
$ docker build -t docker.collmot.com/skybrush-server:latest \
    -f etc/deployment/docker/amd64/Dockerfile \
    --build-arg GIT_ACCESS_TOKEN_USERNAME=username \
	--build-arg GIT_ACCESS_TOKEN_PASSWORD=password .
```

Make sure you replace the username and the password in the command line with a
personal access token that you can set up on the web user interface of
`git.collmot.com`. This is needed so Docker can access the source code of
several of our Python modules during the build process.

To test the container, run this:

```sh
$ docker run -p 5000:5000 -p 4242:4242/udp --rm docker.collmot.com/skybrush-server:latest
```

You may also need to map additional ports depending on your use-case; port 5000 is the
Skybrush server itself, while UDP port 4242 is the one where our drones communicate
with each other.

The configuration of the server in the Docker container may be overridden
by creating a folder and placing a file named `skybrush.cfg` in it, and then
mounting the folder at the `/data` path in the container:

```sh
$ docker run -p 5000:5000 -p 4242:4242/udp -v /path/to/folder:/data --rm docker.collmot.com/skybrush-server:latest
```
