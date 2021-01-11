#!/usr/bin/env python3
#
# Collects the Win32 dependencies of the server into two separate requirements
# file, one for wheels and one for source tarballs

from contextlib import ExitStack
from packaging.markers import Marker

import toml


def main():
    with open("poetry.lock") as fp:
        lockfile = toml.load(fp)

    env = {
        "os_name": "nt",
        "sys_platform": "win32",
        "python_version": "3.7",
        "python_full_version": "3.7.9",
        "implementation_name": "cp"
    }

    stack = ExitStack()
    with ExitStack() as stack:
        req_fp = stack.enter_context(open("requirements.txt", "r"))
        wheels_fp = stack.enter_context(open("requirements-win32-wheels.txt", "w"))
        source_fp = stack.enter_context(open("requirements-win32-source.txt", "w"))

        for line in req_fp:
            line = line.strip()

            if not line or line.startswith("-"):
                fps = (wheels_fp, source_fp)
            else:
                spec, _, markers = line.partition(";")
                if not markers or Marker(markers).evaluate(env):
                    name = spec[:spec.index("=")]
                    if name == "lxml":
                        fps = ()
                    else:
                        files = lockfile["metadata"]["files"][name]
                        has_wheel = any(entry["file"].endswith(".whl") for entry in files)
                        fps = (wheels_fp, ) if has_wheel else (source_fp, )
                        line = spec
                else:
                    fps = ()

            for fp in fps:
                fp.write(line + "\n")

if __name__ == "__main__":
    main()

