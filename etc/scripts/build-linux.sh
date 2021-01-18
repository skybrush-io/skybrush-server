#!/bin/bash
#
# Script that builds Skybrush Server on a Linux machine accessible via ssh
# frontend straight to a Raspberry Pi

OUTPUT_FILE="skybrush-server-linux-dist.tar.gz"
DEBUG=1

###############################################################################

set -e

if [ -f /proc/cpuinfo ]; then
  RUNNING_ON_LINUX=1
else
  RUNNING_ON_LINUX=0
fi

export LANG=C LC_ALL=C

if [ $RUNNING_ON_LINUX -lt 1 ]; then
  PORT=22
  while getopts "p:" OPTION; do
    case $OPTION in
    p)
      PORT=$OPTARG
	  ;;
    *)
      exit 1
      ;;
    esac
  done
  shift $((OPTIND-1))

  # We are not on Linux yet so just copy ourselves to the target machine
  TARGET="$1"
  if [ "x$TARGET" = x ]; then
    echo "Usage: $0 [-p PORT] USERNAME@IP"
    echo ""
    echo "where USERNAME@IP is the username and IP address of the Linux machine."
    echo "We assume that public key authentication is already set up; if it is"
    echo "not, run 'ssh-copy-id USERNAME@IP'"
    exit 1
  fi

  scp -P "$PORT" -q "$0" "$TARGET":.
  ssh -o ForwardAgent=yes "$TARGET" -p "$PORT" /bin/bash <<EOF
set -e
chmod +x ./build-linux.sh
./build-linux.sh
rm ./build-linux.sh
EOF

  exit
fi

##############################################################################

## From now on, this bit of code is supposed to be executed on the target device
## only

# Read the bash profile
source ${HOME}/.profile

# Find out what the current directory is; we will return there are the end of
# the script
CWD=`pwd`

# Check whether we have set up PyArmor already
if [ ! -d ${HOME}/.pyarmor ]; then
  echo "PyArmor not set up on this device yet. Please copy your PyArmor license file "
  echo "and private capsule to ~/.pyarmor/"
  exit 1
fi

# Check whether poetry is installed
if [ ! -d ${HOME}/.poetry ]; then
  echo "Poetry not installed yet. Please install Poetry first with this command:"
  echo ""
  echo "$ curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py | python3 -"
  echo ""
  echo "To allow this script to run in unsupervised mode, you should use an"
  echo "unencrypted keyring (unless you use a GUI on the device)."
  echo ""
  echo "Create or edit ~/.local/share/python_keyring/keyringrc.cfg so that it has"
  echo "the following contents _before_ setting up the credentials:"
  echo ""
  echo "[backend]"
  echo "default-keyring=keyrings.alt.file.PlaintextKeyring"
  echo ""
  echo "You might also need to install python-keyrings.alt"
  echo ""
  echo "After installation, log out, log in again and then set your username and"
  echo "password to the CollMot private repository with:"
  echo ""
  echo "poetry config http-basic.collmot <your-username>"
  exit 1
fi

if [ $DEBUG -lt 1 ]; then
  WORK_DIR=$(mktemp -d -t build-XXXXXXXXXX --tmpdir=.)
else
  WORK_DIR=work
fi

POETRY=${HOME}/.poetry/bin/poetry

echo "Work directory: ${WORK_DIR}"

if [ ! -d "${WORK_DIR}" ]; then
  mkdir -p "${WORK_DIR}"
fi
cd "${WORK_DIR}"

if [ ! -d skybrush-server ]; then
    ssh-keyscan -H git.collmot.com >~/.ssh/known_hosts
    git clone git@git.collmot.com:collmot/flockwave-server.git skybrush-server
fi

cd skybrush-server
git pull
rm -rf dist/pyarmor/*.tar.gz
poetry install
etc/scripts/build-pyarmored-dist.sh --standalone
cd ..

rm -rf staging
mkdir -p staging/skybrush/bin
mkdir -p staging/skybrush/lib
tar -C staging/skybrush --strip-components=1 -xvvzf skybrush-server/dist/pyarmor/*.tar.gz
cp skybrush-server/etc/deployment/configs/skybrush-indoor.jsonc staging/skybrush/skybrush.jsonc
mv staging/skybrush/bin/skybrushd staging/skybrush/lib
cp skybrush-server/etc/deployment/linux/skybrushd staging/skybrush/bin/skybrushd
tar -C staging --owner=0 --group=0 -cvvzf "${OUTPUT_FILE}" skybrush
rm -rf staging

mv "${OUTPUT_FILE}" "${CWD}"
cd "${CWD}"

if [ $DEBUG -lt 1 ]; then
  rm -rf "${WORK_DIR}"
fi

echo ""
echo "------------------------------------------------------------------------"
echo ""
echo "Obfuscated bundle created successfully in ${OUTPUT_FILE}"
echo ""
echo "Don't forget to add a license file after extraction as skybrushd.cml"

