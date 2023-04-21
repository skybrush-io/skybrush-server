#!/usr/bin/env python3

from datetime import datetime
from pathlib import Path
from shutil import make_archive, rmtree

import pdoc
import pdoc.render

here = Path(__file__).parent

if __name__ == "__main__":
    out_dir = here / "build"
    if out_dir.is_dir():
        rmtree(out_dir)

    pdoc.render.configure(
        docformat="google",
    )

    pdoc.pdoc(
        "flockwave",
        "!flockwave.app_framework",  # requires urwid
        "!flockwave.protocols.mavlink",  # not needed, too much cruft
        "!flockwave.server.ext.socketio.vendor",  # vendored code, not ours
        output_directory=out_dir,
    )

    date = datetime.now().strftime("%Y%m%d")
    archive_path = str(here / f"skybrush-server-docs-{date}")
    make_archive(archive_path, "zip", out_dir)

    print(f"Documentation archive created in {archive_path}.zip")
