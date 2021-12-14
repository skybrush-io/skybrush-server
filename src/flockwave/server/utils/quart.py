from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from sys import executable
from typing import Optional
from zlib import adler32

from jinja2 import BaseLoader, ChoiceLoader, TemplateNotFound
from quart import Blueprint, Response, current_app, send_file

from .generic import constant
from .packaging import is_oxidized


def _get_quart_root_path_of(name: str) -> Optional[str]:
    if is_oxidized():
        # Running inside PyOxidizer, return the current folder as a dummy
        # root path for Quart
        return str(Path.cwd())
    else:
        # Running as a "normal" Python application, return None and let
        # Quart sort it out
        return None


_always_true = constant(True)


class PyOxidizerTemplateLoader(BaseLoader):
    """Jinja2 template loader that looks for a template with the given name in
    a given Python package under a subpath.
    """

    def __init__(self, package: str, path: str = "templates", encoding: str = "utf-8"):
        self.package = package
        self.path = path

    def get_source(self, environment, template):
        reader = __loader__.get_resource_reader(self.package)
        if reader is None:
            raise TemplateNotFound(template)
        try:
            # __loader__ points to PyOxidizer's OxidizedFinder
            with reader.open_resource(f"{self.path}/{template}") as fp:
                data = fp.read().decode("utf-8")
        except Exception:
            raise TemplateNotFound(template) from None

        return (data, None, _always_true)


class PyOxidizerBlueprint(Blueprint):
    """PyOxidizer-compatible Quart blueprint class that resolves static assets
    from the PyOxidizer package before falling back to resolving them from the
    filesystem.
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

        # Store the timestamp of the main executable. Static files loaded from
        # the executable will return this timestamp as their last-modified
        # timestamp

    @Blueprint.jinja_loader.getter
    def jinja_loader(self):
        package, _, _ = self.import_name.rpartition(".")
        oxidized_loader = PyOxidizerTemplateLoader(package)
        super_loader = super().jinja_loader
        if super_loader:
            return ChoiceLoader([oxidized_loader, super_loader])
        else:
            return oxidized_loader

    async def send_static_file(self, filename: str) -> Response:
        if not self.static_folder:
            raise RuntimeError("No static folder for this object")

        # __loader__ points to PyOxidizer's OxidizedFinder
        package, _, _ = self.import_name.rpartition(".")
        reader = __loader__.get_resource_reader(package)
        resource_path = f"static/{filename}"

        try:
            if reader is not None:
                data = reader.open_resource(resource_path)
            else:
                data = None
        except Exception:
            data = None

        if not data:
            # Resource not embedded; fall back to static file retrieval
            return await super().send_static_file(filename)

        assert reader is not None

        # Do not use a with block to close data; it must be kept open until
        # the request handler ends
        response: Response = await send_file(data, attachment_filename=Path(filename).name)  # type: ignore

        # Check whether the file is represented by a physical file on the disk
        try:
            path = Path(reader.resource_path(resource_path))
        except Exception:
            path = None

        # If the file is not represented by a physical file on the disk, use the
        # executable of the bundled app
        if not path or not path.is_file():
            path = Path(executable)

        if path and path.is_file():
            # Set ETag and Last-Modified headers
            stat = path.stat()
            etag = "{}-{}-{}".format(stat.st_mtime, stat.st_size, adler32(bytes(path)))
            response.set_etag(etag)
            response.last_modified = stat.st_mtime

        if path:
            # Set max age and expiry date for caching
            cache_timeout = current_app.get_send_file_max_age(str(path))
            if cache_timeout is not None:
                response.cache_control.max_age = cache_timeout
                response.expires = datetime.utcnow() + timedelta(seconds=cache_timeout)

        return response


def make_blueprint(name, import_name, *args, **kwds) -> Blueprint:
    """Creates a Quart blueprint that takes into account whether we are running
    in a PyOxidizer-enabled distribution or not.
    """
    if "root_path" not in kwds:
        kwds["root_path"] = _get_quart_root_path_of(name)

    cls = PyOxidizerBlueprint if is_oxidized() else Blueprint
    return cls(name, import_name, *args, **kwds)
