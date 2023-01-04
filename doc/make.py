#!/usr/bin/env python3

from pathlib import Path
from shutil import rmtree

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
        "skybrush",
        "!flockwave.protocols.mavlink",
        "!flockwave.server.ext.socketio.vendor",
        output_directory=out_dir,
    )
