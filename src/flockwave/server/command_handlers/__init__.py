"""Module containing implementations of common command handlers that are used
by multiple UAV drivers.
"""

from .color import create_color_command_handler
from .parameters import create_parameter_command_handler
from .version import create_version_command_handler

__all__ = (
    "create_color_command_handler",
    "create_parameter_command_handler",
    "create_version_command_handler",
)
