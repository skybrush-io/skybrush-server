from __future__ import annotations

from typing import Awaitable, Callable, Iterable, TypeAlias

from flockwave.server.logger import log as base_log
from flockwave.server.show import (
    ShowSpecification,
    SkybrushBinaryShowFile,
    get_coordinate_system_from_show_specification,
    get_light_program_from_show_specification,
    get_trajectory_from_show_specification,
)

from .builder import ShowFileBuilder

__all__ = ("encode_show", "ShowEncodingHook", "ShowFileBuilder")

log = base_log.getChild("show.encoding")

_EXTENSION_HANDLED_PROPS = frozenset({"pyro", "rthPlan", "yawControl"})
"""Props in a show specification that are not handled by the show extension
itself but by other extensions via encoding hooks. The user is warned during
the show encoding process if such a prop is present in the show specification
and no encoding hook marked it as handled."""

ShowEncodingHook: TypeAlias = Callable[
    [ShowSpecification, ShowFileBuilder], Awaitable[None]
]
"""Type alias for hook functions that can be called during the show encoding process.

These functions take the show specification being encoded and a `ShowFileBuilder` as
their arguments (in this order) and return an awaitable that completes when the hook
has finished processing. The builder can be used to extend the encoding process, for
example by adding additional blocks to the show file being constructed or by adding
events to the shared, deferred event list of the show file. Direct access to the
underlying show file (`builder.show_file`) is possible but it is a low-level feature
that should be avoided if possible.
"""


async def encode_show(
    show: ShowSpecification,
    *,
    hooks: Iterable[ShowEncodingHook] | None = None,
) -> SkybrushBinaryShowFile:
    """Encodes a show specification into Skybrush binary format.

    Args:
        show: the show specification to encode.
        hooks: optional iterable of hook functions to be called during the encoding
            process.

    Returns:
        the encoded show file
    """
    coordinate_system = get_coordinate_system_from_show_specification(show)
    if coordinate_system.type != "nwu":
        raise RuntimeError("Only NWU coordinate systems are supported")

    light_program = get_light_program_from_show_specification(show)
    trajectory = get_trajectory_from_show_specification(show)

    async with ShowFileBuilder.create_in_memory() as builder:
        await builder.add_trajectory(trajectory)
        await builder.add_encoded_light_program(light_program)
        for hook in hooks or ():
            await hook(show, builder)

        unhandled_props = (
            set(show.keys()) & _EXTENSION_HANDLED_PROPS
        ) - builder.handled
        if unhandled_props:
            log.warning(
                "The following parts of the show specification were not "
                "handled by any extension and will be neglected: "
                + ", ".join(sorted(unhandled_props))
            )

    return builder.show_file
