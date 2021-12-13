#!/bin/bash

set -e

# Get the script root
SCRIPT_ROOT=$(dirname "$0")
SCRIPT_ROOT=$( cd "${SCRIPT_ROOT}"; pwd )
REPO_ROOT="${SCRIPT_ROOT}/../../.."

cd "${REPO_ROOT}"

function printUsage() {
  echo -e "\033[1mUsage:\033[0m"
  echo "$0 [STAGING_DIR]"
  echo
  echo -e "\033[1mOptions:\033[0m"
  echo "  -h (--help)"
  echo
  echo -e "\033[1mExample::\033[0m"
  echo "$0 build/pyarmor/staging"
}

# Argument validation
if [[ "$1" == "-h" ||  "$1" == "--help" ]]; then
    printUsage
    exit 1
fi
if [ -z "$1" ]; then
    echo "Please enter a valid staging directory for your application"
    echo
    printUsage
    exit 1
else
    echo "Staging Directory: $1"
fi

echo ""

# Parameters
STAGING_DIRECTORY="${1}"
TARGET_DIRECTORY="${SCRIPT_ROOT}/target"
PRODUCT="skybrush-server"
PRODUCT_DISPLAY_NAME="Skybrush Server"
LAUNCHER="skybrushd"

# Extract the version number from pyproject.toml
VERSION=`cat pyproject.toml|grep ^version|head -1|cut -d '"' -f 2`

#Functions
log_info() {
    echo "[INFO]" $1
}

log_warn() {
    echo "[WARN]" $1
}

log_error() {
    echo "[ERROR]" $1
}

deleteInstallationDirectory() {
    log_info "Cleaning $TARGET_DIRECTORY directory."
    rm -rf $TARGET_DIRECTORY

    if [[ $? != 0 ]]; then
        log_error "Failed to clean $TARGET_DIRECTORY directory" $?
        exit 1
    fi
}

createInstallationDirectory() {
    if [ -d ${TARGET_DIRECTORY} ]; then
        deleteInstallationDirectory
    fi
    mkdir $TARGET_DIRECTORY

    if [[ $? != 0 ]]; then
        log_error "Failed to create $TARGET_DIRECTORY directory" $?
        exit 1
    fi
}

copyFilesDirectory(){
  createInstallationDirectory
  cp -r ${SCRIPT_ROOT}/files ${TARGET_DIRECTORY}/
  chmod -R 755 ${TARGET_DIRECTORY}/files/scripts
  chmod -R 755 ${TARGET_DIRECTORY}/files/Resources
  chmod 755 ${TARGET_DIRECTORY}/files/Distribution
  mv ${TARGET_DIRECTORY}/files ${TARGET_DIRECTORY}/darwin
}

copyBuildDirectory() {
    sed -i '' -e "s/__VERSION__/${VERSION}/g" ${TARGET_DIRECTORY}/darwin/scripts/postinstall
    sed -i '' -e "s/__PRODUCT__/${PRODUCT}/g" ${TARGET_DIRECTORY}/darwin/scripts/postinstall
    sed -i '' -e "s/__PRODUCT_DISPLAY_NAME__/${PRODUCT_DISPLAY_NAME}/g" ${TARGET_DIRECTORY}/darwin/scripts/postinstall
    sed -i '' -e "s/__LAUNCHER__/${LAUNCHER}/g" ${TARGET_DIRECTORY}/darwin/scripts/postinstall
    chmod -R 755 ${TARGET_DIRECTORY}/darwin/scripts/postinstall

    sed -i '' -e "s/__VERSION__/${VERSION}/g" ${TARGET_DIRECTORY}/darwin/Distribution
    sed -i '' -e "s/__PRODUCT__/${PRODUCT}/g" ${TARGET_DIRECTORY}/darwin/Distribution
    sed -i '' -e "s/__PRODUCT_DISPLAY_NAME__/${PRODUCT_DISPLAY_NAME}/g" ${TARGET_DIRECTORY}/darwin/Distribution
    sed -i '' -e "s/__LAUNCHER__/${LAUNCHER}/g" ${TARGET_DIRECTORY}/darwin/Distribution
    # chmod -R 755 ${TARGET_DIRECTORY}/darwin/Distribution

    sed -i '' -e "s/__VERSION__/${VERSION}/g" ${TARGET_DIRECTORY}/darwin/Resources/*.html
    sed -i '' -e "s/__PRODUCT__/${PRODUCT}/g" ${TARGET_DIRECTORY}/darwin/Resources/*.html
    sed -i '' -e "s/__PRODUCT_DISPLAY_NAME__/${PRODUCT_DISPLAY_NAME}/g" ${TARGET_DIRECTORY}/darwin/Resources/*.html
    sed -i '' -e "s/__LAUNCHER__/${LAUNCHER}/g" ${TARGET_DIRECTORY}/darwin/Resources/*.html
    # chmod -R 755 ${TARGET_DIRECTORY}/darwin/Resources/

    rm -rf ${TARGET_DIRECTORY}/darwinpkg
    mkdir -p ${TARGET_DIRECTORY}/darwinpkg

    # Copy files to /usr/local/opt
    mkdir -p ${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}
    cp -a ${STAGING_DIRECTORY}/. ${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}
    # chmod -R 755 ${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}

    rm -rf ${TARGET_DIRECTORY}/package
    mkdir -p ${TARGET_DIRECTORY}/package
    # chmod -R 755 ${TARGET_DIRECTORY}/package

    rm -rf ${TARGET_DIRECTORY}/pkg
    mkdir -p ${TARGET_DIRECTORY}/pkg
    # chmod -R 755 ${TARGET_DIRECTORY}/pkg
}

function addIconsInApplicationsFolder() {
    mkdir -p "${TARGET_DIRECTORY}/darwinpkg/Applications/${PRODUCT_DISPLAY_NAME}"
    cat >"${TARGET_DIRECTORY}/darwinpkg/Applications/${PRODUCT_DISPLAY_NAME}/${PRODUCT_DISPLAY_NAME}.command" <<EOF
#/bin/sh
clear
/usr/local/opt/${PRODUCT}/current/bin/${LAUNCHER}
EOF
    cat >"${TARGET_DIRECTORY}/darwinpkg/Applications/${PRODUCT_DISPLAY_NAME}/Uninstall ${PRODUCT_DISPLAY_NAME}.command" <<EOF
#/bin/sh
clear
/usr/local/opt/${PRODUCT}/current/bin/uninstall.sh
EOF
    chmod a+x "${TARGET_DIRECTORY}/darwinpkg/Applications/${PRODUCT_DISPLAY_NAME}"/*.command
}

function buildPackage() {
    log_info "Application installer package building started. (1/3)"
    pkgbuild --identifier com.collmot.${PRODUCT}.${VERSION} \
    --version ${VERSION} \
    --scripts ${TARGET_DIRECTORY}/darwin/scripts \
    --root ${TARGET_DIRECTORY}/darwinpkg \
    ${TARGET_DIRECTORY}/package/${PRODUCT}.pkg > /dev/null 2>&1
}

function buildProduct() {
    log_info "Application installer product building started. (2/3)"
    productbuild --distribution ${TARGET_DIRECTORY}/darwin/Distribution \
    --resources ${TARGET_DIRECTORY}/darwin/Resources \
    --package-path ${TARGET_DIRECTORY}/package \
    ${TARGET_DIRECTORY}/pkg/$1 > /dev/null 2>&1
}

function signProduct() {
    log_info "Application installer signing process started. (3/3)"
    mkdir -p ${TARGET_DIRECTORY}/pkg-signed
    # chmod -R 755 ${TARGET_DIRECTORY}/pkg-signed

    read -p "Please enter the Apple Developer Installer Certificate ID:" APPLE_DEVELOPER_CERTIFICATE_ID
    productsign --sign "Developer ID Installer: ${APPLE_DEVELOPER_CERTIFICATE_ID}" \
    ${TARGET_DIRECTORY}/pkg/$1 \
    ${TARGET_DIRECTORY}/pkg-signed/$1

    pkgutil --check-signature ${TARGET_DIRECTORY}/pkg-signed/$1
}

function createInstaller() {
    log_info "Application installer generation process started. (3 Steps)"
    buildPackage
    buildProduct ${PRODUCT}-macos-installer-x64-${VERSION}.pkg
    # while true; do
    #     read -p "Do you wish to sign the installer (You should have Apple Developer Certificate) [y/N]? " answer
    #     [[ $answer == "y" || $answer == "Y" ]] && FLAG=true && break
    #     [[ $answer == "n" || $answer == "N" || $answer == "" ]] && log_info "Skipped signing process." && FLAG=false && break
    #     echo "Please answer with 'y' or 'n'"
    # done
    # [[ $FLAG == "true" ]] && signProduct ${PRODUCT}-macos-installer-x64-${VERSION}.pkg
    log_info "Application installer signing process skipped. (3/3)"

    mkdir -p dist/macos
    mv "${TARGET_DIRECTORY}"/pkg/*.pkg "dist/macos/${PRODUCT_DISPLAY_NAME} ${VERSION}.pkg"
    rm -rf "${TARGET_DIRECTORY}"
}

function createUninstaller(){
    cp ${SCRIPT_ROOT}/files/Resources/uninstall.sh ${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}/bin
    chmod 0755 "${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}/bin/uninstall.sh"
    sed -i '' -e "s/__VERSION__/${VERSION}/g" "${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}/bin/uninstall.sh"
    sed -i '' -e "s/__PRODUCT__/${PRODUCT}/g" "${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}/bin/uninstall.sh"
    sed -i '' -e "s/__PRODUCT_DISPLAY_NAME__/${PRODUCT_DISPLAY_NAME}/g" "${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}/bin/uninstall.sh"
    sed -i '' -e "s/__LAUNCHER__/${LAUNCHER}/g" "${TARGET_DIRECTORY}/darwinpkg/usr/local/opt/${PRODUCT}/${VERSION}/bin/uninstall.sh"
}

#Main script
log_info "macOS installer generation started."

copyFilesDirectory
copyBuildDirectory
addIconsInApplicationsFolder
createUninstaller
createInstaller

log_info "macOS installer generation finished"
exit 0
