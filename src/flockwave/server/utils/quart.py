from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from sys import executable
from typing import IO, Protocol
from zlib import adler32

from jinja2 import BaseLoader, ChoiceLoader, FileSystemLoader, TemplateNotFound
from quart import Blueprint, Response, current_app, send_file
from werkzeug.utils import cached_property

from .generic import constant
from .packaging import is_oxidized

__all__ = ("open_package_resource",)


def _get_quart_root_path_of(name: str) -> str | None:
    if is_oxidized():
        # Running inside PyOxidizer, return the current folder as a dummy
        # root path for Quart
        return str(Path.cwd())
    else:
        # Running as a "normal" Python application, return None and let
        # Quart sort it out
        return None


_always_true = constant(True)


class ResourceReader(Protocol):
    """Protocol describing the resource reader objects returned by the
    ``get_resource_reader()`` method of PyOxidizer's ``OxidizedFinder``.

    This used to be covered by ``importlib.abc.ResourceReader``, but that
    class was removed in Python 3.14.
    """

    def open_resource(self, resource: str) -> IO[bytes]: ...
    def resource_path(self, resource: str) -> str: ...


def _get_oxidized_resource_reader(package: str) -> ResourceReader | None:
    """Returns the resource reader of PyOxidizer's ``OxidizedFinder`` for the
    given package, or ``None`` if the package has no embedded resources.
    """
    # __loader__ points to PyOxidizer's OxidizedFinder
    assert hasattr(__loader__, "get_resource_reader")
    assert callable(__loader__.get_resource_reader)
    return __loader__.get_resource_reader(package)  # ty:ignore[invalid-return-type]


def open_package_resource(package: str, path: str) -> IO[bytes]:
    """Opens a resource of a Python package for reading in binary mode.

    Unlike ``importlib.resources.files()``, this function also works for
    resources in nested subfolders of the package when running inside
    PyOxidizer. ``files()`` falls back to a compatibility adapter there that
    can only resolve resources at the top level of the package.

    Parameters:
        package: the name of the package that contains the resource
        path: the path of the resource relative to the package, using forward
            slashes as separators

    Raises:
        OSError: if the resource does not exist or cannot be opened
    """
    if is_oxidized():
        reader = _get_oxidized_resource_reader(package)
        if reader is not None:
            try:
                return reader.open_resource(path)
            except Exception:
                # Resource not embedded; fall back to the filesystem
                pass

    return (files(package) / path).open("rb")


class PyOxidizerTemplateLoader(BaseLoader):
    """Jinja2 template loader that looks for a template with the given name in
    a given Python package under a subpath.
    """

    def __init__(self, package: str, path: str = "templates", encoding: str = "utf-8"):
        self.package = package
        self.path = path

    def get_source(self, environment, template):
        reader = _get_oxidized_resource_reader(self.package)
        if reader is None:
            raise TemplateNotFound(template)
        try:
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

    # jinja_loader getter copied from flask.sansio.scaffold as we cannot override
    # a cached property since Werkzeug 3.x for some reason

    @cached_property
    def jinja_loader(self) -> BaseLoader | None:
        package, _, _ = self.import_name.rpartition(".")
        oxidized_loader = PyOxidizerTemplateLoader(package)
        if self.template_folder is not None:
            super_loader = FileSystemLoader(
                os.path.join(self.root_path, self.template_folder)
            )
        else:
            super_loader = None
        if super_loader:
            return ChoiceLoader([oxidized_loader, super_loader])
        else:
            return oxidized_loader

    async def send_static_file(self, filename: str) -> Response:
        if not self.static_folder:
            raise RuntimeError("No static folder for this object")

        package, _, _ = self.import_name.rpartition(".")
        reader = _get_oxidized_resource_reader(package)
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
        response: Response = await send_file(
            data,  # ty:ignore[invalid-argument-type]
            attachment_filename=Path(filename).name,
        )

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
            response.last_modified = datetime.fromtimestamp(stat.st_mtime)

        if path:
            # Set max age and expiry date for caching
            cache_timeout = current_app.get_send_file_max_age(str(path))
            if cache_timeout is not None:
                response.cache_control.max_age = cache_timeout
                response.expires = datetime.now(timezone.utc) + timedelta(
                    seconds=cache_timeout
                )

        return response


def make_blueprint(name, import_name, *args, **kwds) -> Blueprint:
    """Creates a Quart blueprint that takes into account whether we are running
    in a PyOxidizer-enabled distribution or not.
    """
    if "root_path" not in kwds:
        kwds["root_path"] = _get_quart_root_path_of(name)

    cls = PyOxidizerBlueprint if is_oxidized() else Blueprint
    return cls(name, import_name, *args, **kwds)
