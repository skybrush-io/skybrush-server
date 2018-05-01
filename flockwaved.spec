# -*- mode: python -*-

import os
import sys

block_cipher = None

# Prevent TkInter to be included in the bundle, step 1
sys.modules['FixTk'] = None

# Fix for my local dev machine with a convoluted symlink structure (ntamas)
root_dir = os.getcwd()
if root_dir.startswith("/Volumes/Macintosh HD/ntamas"):
	root_dir = root_dir.replace("/Volumes/Macintosh HD", "/Users")

a = Analysis([os.path.join(root_dir, 'bin', 'flockwaved')],
             pathex=[root_dir],
             binaries=[],
             datas=[],
             hiddenimports=['colorlog'],
             hookspath=[],
             runtime_hooks=[],
             excludes=['FixTk', 'tcl', 'tk', '_tkinter', 'tkinter', 'Tkinter'],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='flockwaved',
          debug=False,
          strip=False,
          upx=True,
          console=True )
coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               name='flockwaved')
