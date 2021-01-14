#!python3.7-32
# Launcher script for Skybrush Server on Win32

import os
import site
import sys

scriptdir, script = os.path.split(__file__)
scriptdir = os.path.abspath(scriptdir or ".")
installdir = scriptdir  # for compatibility with commands

pkgdir = os.path.join(scriptdir, "pkgs")
libdir = os.path.join(scriptdir, "lib")

sys.path.insert(0, pkgdir)
os.environ["PYTHONPATH"] = pkgdir + os.pathsep + os.environ.get("PYTHONPATH", "")

try:
    os.add_dll_directory(libdir)
except AttributeError:
    os.environ["PATH"] = libdir + os.pathsep + os.environ.get("PATH", "")

appdata = os.environ.get("APPDATA", None) or os.path.expanduser("~")

if "pythonw" in sys.executable:
    sys.stdout = sys.stderr = open(os.path.join(appdata, script + ".log"), "w", errors="replace")


if __name__ == "__main__":
    from flockwave.server.launcher import start
    os.chdir(scriptdir)
    sys.exit(start())

