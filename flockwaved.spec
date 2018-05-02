# -*- mode: python -*-

import os
import sys

block_cipher = None
single_file = True

###########################################################################

# Prevent TkInter to be included in the bundle, step 1
sys.modules["FixTk"] = None

# Fix for my local dev machine with a convoluted symlink structure (ntamas)
root_dir = os.getcwd()
if root_dir.startswith("/Volumes/Macintosh HD/ntamas"):
    root_dir = root_dir.replace("/Volumes/Macintosh HD", "/Users")

# Extra modules to import
extra_modules = [
    "engineio.async_eventlet",
    "flockwave.server.config"
]

# Modules to exclude
exclude_modules = [
    # No Tcl/Tk
    "FixTk", "tcl", "tk", "_tkinter", "tkinter", "Tkinter",
    # Prevent a Jinja2 module from being imported in Python <3.6 where it
    # would be unsupported
    "jinja2.asyncsupport"
]

# Parse default configuration
config_file = os.path.join(root_dir, "flockwave", "server", "config.py")
config = {}
exec(
    compile(
        open(config_file).read(), "config.py", mode="exec", dont_inherit=True
    ),
    None,
    config
)

# Make sure to include all extensions mentioned in the config
extra_modules += [
    "flockwave.server.ext.{0}".format(ext_name)
    for ext_name in config["EXTENSIONS"]
    if not ext_name.startswith("_")
]

# Add some extension-dependent dependencies
if sys.platform.lower().startswith("linux") and \
        "smpte_timecode" in config["EXTENSIONS"]:
    extra_modules.append("mido.backends.rtmidi")

# Now comes the PyInstaller dance
a = Analysis(
    [os.path.join(root_dir, "bin", "flockwaved")],
    pathex=[root_dir],
    binaries=[],
    datas=[],
    hiddenimports=extra_modules,
    hookspath=[
        os.path.join(root_dir, "etc", "deployment")
    ],
    runtime_hooks=[
        os.path.join(root_dir, "etc", "deployment", "runtime_hook.py")
    ],
    excludes=exclude_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if single_file:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name="flockwaved",
        debug=False,
        strip=False,
        upx=True,
        runtime_tmpdir=None,
        console=True
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        name="flockwaved",
        debug=False,
        strip=False,
        upx=True,
        console=True
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        name="flockwaved"
    )
