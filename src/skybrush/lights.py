"""Temporary place for functions that are related to the processing of
Skybrush-related light programs, until we find a better place for them.
"""

from base64 import b64decode
from typing import Dict

__all__ = ("get_skybrush_light_program_from_show_specification",)


def get_skybrush_light_program_from_show_specification(show: Dict) -> bytes:
    """Returns the raw Skybrush light program as bytecode from the given
    show specification object.
    """
    lights = show.get("lights", None)
    light_version = lights.get("version", 0)
    if light_version != 1:
        raise RuntimeError("only version 1 light programs are supported")
    light_data = b64decode(lights["data"])
    return light_data
