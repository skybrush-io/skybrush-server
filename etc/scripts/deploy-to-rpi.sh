#!/bin/bash
#
# Script that builds and deploys the Skybrush Server along with a console
# frontend straight to a Raspberry Pi

OUTPUT_FILE="skybrush-server-rpi-dist.tar.gz"
DEBUG=1

###############################################################################

set -e

if [ -f /proc/cpuinfo ]; then
  RUNNING_ON_RPI=`grep Hardware /proc/cpuinfo | grep -E -c 'BCM|Versatile'`
else
  RUNNING_ON_RPI=0
fi

export LANG=C LC_ALL=C

if [ $RUNNING_ON_RPI -lt 1 ]; then
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

  # We are not on the Raspberry Pi yet so just copy ourselves to the RPi
  TARGET="$1"
  if [ "x$TARGET" = x ]; then
    echo "Usage: $0 [-p PORT] USERNAME@IP"
    echo ""
    echo "where USERNAME@IP is the username and IP address of the Raspberry Pi."
    echo "We assume that public key authentication is already set up; if it is"
    echo "not, run 'ssh-copy-id USERNAME@IP'"
    exit 1
  fi

  scp -P "$PORT" -q "$0" "$TARGET":.
  ssh -o ForwardAgent=yes "$TARGET" -p "$PORT" /bin/bash <<EOF
set -e
chmod +x ./deploy-to-rpi.sh
./deploy-to-rpi.sh
rm ./deploy-to-rpi.sh
EOF

  exit
fi

##############################################################################

## From oow on, this bit of code is supposed to be exected on the RPi only

# Read the bash profile
source ${HOME}/.profile

# Find out what the current directory is; we will return there are the end of
# the script
CWD=`pwd`

# Check whether we have set up PyArmor already
if [ ! -d ${HOME}/.pyarmor ]; then
  echo "PyArmor not set up on the RPi yet. Please copy your PyArmor license file "
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
  echo "unencrypted keyring (unless you use a GUI on the RPi, which you shouldn't)."
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
etc/scripts/build-pyarmored-dist.sh
cd ..

if [ ! -d skybrush-console-frontend ]; then
    git clone git@git.collmot.com:skybrush/console-frontend.git skybrush-console-frontend
fi

cd skybrush-console-frontend
git pull
rm -rf dist/pyarmor/*.tar.gz
etc/scripts/build-pyarmored-dist.sh
cd ..

rm -rf staging
mkdir -p staging/opt/skybrush/server
tar -C staging/opt/skybrush/server --strip-components=1 -xvvzf skybrush-server/dist/pyarmor/*.tar.gz
mkdir -p staging/opt/skybrush/frontend
tar -C staging/opt/skybrush/frontend --strip-components=1 -xvvzf skybrush-console-frontend/dist/pyarmor/*.tar.gz
mkdir -p staging/opt/skybrush/boot
cp skybrush-server/etc/deployment/rpi/run-tasks-at-boot staging/opt/skybrush/boot/run-tasks-at-boot
chmod a+x staging/opt/skybrush/boot/run-tasks-at-boot
mkdir -p staging/opt/skybrush/config
cp skybrush-server/etc/deployment/rpi/skybrush-console-frontend.json staging/opt/skybrush/config/frontend.json
mkdir -p staging/boot/collmot
cp skybrush-server/etc/deployment/rpi/skybrush.json staging/boot/collmot/skybrush.json
cp skybrush-server/etc/deployment/rpi/network.cfg staging/boot/collmot/network.cfg
mkdir -p staging/etc/systemd/system/network.target.wants
cp skybrush-server/etc/deployment/rpi/collmot-init.service staging/etc/systemd/system/collmot-init.service
ln -s /etc/systemd/system/collmot-init.service staging/etc/systemd/system/network.target.wants/collmot-init.service
mkdir -p staging/etc/systemd/system/getty@tty1.service.d
cp skybrush-server/etc/deployment/rpi/tty1-override.conf staging/etc/systemd/system/getty@tty1.service.d/10-skybrush.conf
mkdir -p staging/etc/ufw/applications.d
cp skybrush-server/etc/deployment/rpi/ufw.conf staging/etc/ufw/applications.d/skybrush-server
tar -C staging --owner=0 --group=0 -cvvzf "${OUTPUT_FILE}" boot etc opt
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
echo "To install it, type the following commands as root:"
echo ""
echo "rm -rf /opt/skybrush"
echo "tar -C / -xvvzf ${CWD}/${OUTPUT_FILE}"
echo ""
echo "After that you should reboot the RPi. Also, don't forget to add a license"
echo "file to the RPi in /boot/collmot/skybrushd.cml"

