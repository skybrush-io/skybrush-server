"""Code that should be executed by PyInstaller before launching the
Flockwave server bundle.
"""

import os

os.environ["EVENTLET_NO_GREENDNS"] = "yes"
