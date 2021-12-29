#!/bin/bash

# Check whether the uninstaller is running as root
if (( $EUID != 0 )); then
    echo "The uninstaller must be run with system administrator privileges."
    echo "You may be asked for the system administrator password now."
    echo ""
    sudo bash "$0" "$@"
    exit
fi

# Installation root
INSTALL_ROOT=/usr/local

# Temporary folder
TMP_ROOT=/tmp

# Need to replace these with install preparation script
VERSION=__VERSION__
PRODUCT=__PRODUCT__

# Parameters
PRODUCT_HOME=${INSTALL_ROOT}/opt/__PRODUCT__
PRODUCT_VERSIONED_HOME=${PRODUCT_HOME}/__VERSION__
LAUNCHER=${INSTALL_ROOT}/bin/__LAUNCHER__
LICENSE_FILE=skybrushd.cml

echo ""
echo "Welcome to the __PRODUCT_DISPLAY_NAME__ Uninstaller"
echo ""
echo "The following packages will be REMOVED:"
echo ""
echo "  * __PRODUCT_DISPLAY_NAME__ __VERSION__"
echo ""
while true; do
    read -p "Do you wish to continue [Y/n]? " answer
    [[ $answer == "y" || $answer == "Y" || $answer == "" ]] && break
    [[ $answer == "n" || $answer == "N" ]] && exit 0
    echo "Please answer with 'y' or 'n'"
done

echo ""

# Forget from pkgutil
pkgutil --forget "com.collmot.$PRODUCT.$VERSION" > /dev/null 2>&1
if [ $? -eq 0 ]
then
  echo "[1/3] [DONE] Successfully deleted installer receipt"
else
  echo "[1/3] [ERROR] Could not delete installer receipt" >&2
fi

# Move license file to a temporary location
rm -f "${TMP_ROOT}/${LICENSE_FILE}.backup"
[ -f "${PRODUCT_VERSIONED_HOME}/${LICENSE_FILE}" ] && mv "${PRODUCT_VERSIONED_HOME}/${LICENSE_FILE}" "${TMP_ROOT}/${LICENSE_FILE}.backup"

# Remove application source distribution
[ -e "${PRODUCT_VERSIONED_HOME}" ] && rm -rf "${PRODUCT_VERSIONED_HOME}"
if [ $? -eq 0 ]
then
  echo "[2/3] [DONE] Successfully deleted application"
else
  echo "[2/3] [ERROR] Could not delete application" >&2
fi

# Check for remaining versions
REMAINING_VERSIONS=`find ${PRODUCT_HOME} -type d -maxdepth 1 -mindepth 1 -exec basename {} ';' | sort -n`
if [ "x${REMAINING_VERSIONS}" != x ]; then
  MOST_RECENT_VERSION=""
  for VERSION in ${REMAINING_VERSIONS}; do
    MOST_RECENT_VERSION="${VERSION}"
  done

  if [ "x${MOST_RECENT_VERSION}" != x -a ! -e "${PRODUCT_HOME}/current" ]; then
    ( cd "${PRODUCT_HOME}"; rm -f current && ln -s "${MOST_RECENT_VERSION}" current ) > /dev/null 2>&1
    if [ $? -eq 0 ]
    then
      echo "[3/3] [DONE] Activated version ${MOST_RECENT_VERSION}"
    else
      echo "[3/3] [DONE] Failed to activate version ${MOST_RECENT_VERSION}"
    fi
    if [ -f "${TMP_ROOT}/${LICENSE_FILE}.backup" -a ! -f "${PRODUCT_HOME}/current/${LICENSE_FILE}" ]
    then
      mv "${TMP_ROOT}/${LICENSE_FILE}.backup" "${PRODUCT_HOME}/current" > /dev/null 2>&1
      if [ $? -ne 0 ]
      then
        echo "      [WARN] Failed to move license file to version ${MOST_RECENT_VERSION}"
      fi
    fi
  fi

  echo ""
  echo "The following versions are still installed on your system:"
  echo ""

  MOST_RECENT_VERSION=""
  for VERSION in ${REMAINING_VERSIONS}; do
    echo "  * __PRODUCT_DISPLAY_NAME__ ${VERSION}"
  done
else
  # No remaining versions, remove the entire product home
  rm -rf "${PRODUCT_HOME}"

  # Remove link to shortcut file
  rm -rf "${LAUNCHER}" "/Applications/__PRODUCT_DISPLAY_NAME__" > /dev/null 2>&1
  if [ $? -eq 0 ]
  then
    echo "[3/3] [DONE] Successfully deleted launcher script"
  else
    echo "[3/3] [ERROR] Could not delete launcher script" >&2
  fi
fi

# Clean up
rm -f "${TMP_ROOT}/${LICENSE_FILE}.backup"

echo ""
echo "Uninstallation finished."
exit 0
