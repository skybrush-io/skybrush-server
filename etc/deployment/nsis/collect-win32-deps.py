#!/usr/bin/env python3
#
# Collects the Win32 dependencies of the server into two separate requirements
# file, one for wheels and one for source tarballs

from contextlib import ExitStack
from packaging.markers import Marker

import sys
import toml


excluded_wheels = {"python_json_logger"}


def main():
    if len(sys.argv) < 2:
        print("Usage: {0} python-version".format(sys.argv[0]))
        sys.exit(1)

    python_full_version = str(sys.argv[1])
    python_version, _, _ = python_full_version.rpartition(".")

    with open("poetry.lock") as fp:
        lockfile = toml.load(fp)

    env = {
        "os_name": "nt",
        "sys_platform": "win32",
        "python_version": python_version,
        "python_full_version": python_full_version,
        "implementation_name": "cp",
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
                # HACK HACK HACK: remove markers because Poetry seems to include
                # incorrect rules there
                markers = ""
                if not markers or Marker(markers).evaluate(env):
                    name = spec[: spec.index("=")]
                    if name in (
                        "netifaces",  # does not have wheel for Python 3.9 yet
                        "pyobjc-core",
                        "pyobjc-framework-cocoa",
                        "pyobjc-framework-systemconfiguration",
                        "pyudev",
                    ):
                        fps = ()
                    else:
                        files = lockfile["metadata"]["files"][name]
                        has_wheel = name not in excluded_wheels and any(
                            entry["file"].endswith(".whl") for entry in files
                        )
                        fps = (wheels_fp,) if has_wheel else (source_fp,)
                        line = spec
                else:
                    fps = ()

            for fp in fps:
                fp.write(line + "\n")


if __name__ == "__main__":
    main()
