"""Temporary place for functions that are related to the processing of
Skybrush-related light programs, until we find a better place for them.
"""

from base64 import b64decode
from typing import Dict

__all__ = ("get_light_program_from_show_specification",)


def get_light_program_from_show_specification(show: Dict) -> bytes:
    """Returns the raw Skybrush light program as bytecode from the given
    show specification object.
    """
    lights = show.get("lights", None)
    version = lights.get("version", 0)
    if version is None:
        raise RuntimeError("light program must have a version number")
    if version != 1:
        raise RuntimeError("only version 1 light programs are supported")
    light_data = b64decode(lights["data"])
    return light_data
