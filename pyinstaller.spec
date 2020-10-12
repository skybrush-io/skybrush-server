# -*- mode: python -*-

import os
import sys

block_cipher = None
single_file = True
name = "skybrushd"

###########################################################################

# Prevent TkInter to be included in the bundle, step 1
sys.modules["FixTk"] = None

# Extra modules to import
extra_modules = set([
    "flockwave.server.config"
])

# Modules to exclude
exclude_modules = [
    # No Tcl/Tk
    "FixTk", "tcl", "tk", "_tkinter", "tkinter", "Tkinter"
]

# Parse default configuration
root_dir = Path.cwd()
config_file = str(root_dir / "flockwave" / "server" / "config.py")
config = {}
exec(
    compile(
        open(config_file).read(), "config.py", mode="exec", dont_inherit=True
    ),
    None,
    config
)

# Make sure to include all extensions mentioned in the config
def extension_module(name):
    return "flockwave.server.ext.{0}".format(name)

extra_modules.update(
    extension_module(ext_name)
    for ext_name in config["EXTENSIONS"]
    if not ext_name.startswith("_")
)

# Prepare the dependency table
dependencies = {
    "system_clock": [extension_module("clocks")]
}
if sys.platform.lower().startswith("linux"):
    dependencies["smpte_timecode"] = ["mido.backends.rtmidi"]

# Add some extension-dependent dependencies
for ext_name in config["EXTENSIONS"]:
    if ext_name in dependencies:
        extra_modules.update(dependencies[ext_name])

# Now comes the PyInstaller dance
a = Analysis(
    [str(root_dir / "bin" / name)],
    pathex=[str(root_dir / "src")],
    binaries=[],
    datas=[],
    hiddenimports=sorted(extra_modules),
    hookspath=[root_dir / "etc" / "deployment"],
    runtime_hooks=[root_dir / "etc" / "deployment" / "runtime_hook.py"],
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
        name=name,
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
        name=name,
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
        name=name
    )
