import sys

if __name__ == "__main__":
    # Do not use relative imports here; it will confuse PyInstaller
    from flockwave.server.launcher import start

    sys.exit(start())
