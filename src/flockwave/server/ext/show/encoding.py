from __future__ import annotations

from logging import Logger
from typing import TYPE_CHECKING, Awaitable, Callable, Iterable, TypeAlias

from flockwave.ext.errors import NoSuchExtension

from flockwave.server.show import (
    ShowSpecification,
    SkybrushBinaryShowFile,
    get_coordinate_system_from_show_specification,
    get_light_program_from_show_specification,
    get_trajectory_from_show_specification,
)

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


__all__ = ("encode_show", "ShowEncodingHook")

ShowEncodingHook: TypeAlias = Callable[
    [SkybrushBinaryShowFile, ShowSpecification], Awaitable[None]
]
"""Type alias for hook functions that can be called during the show encoding process.

These functions take a `ShowSpecification` and a `SkybrushBinaryShowFile` as arguments
and return an awaitable that completes when the hook has finished processing. The hooks
can extend or modify the encoding process, for example by adding additional data to the
show file.
"""


async def encode_show(
    show: ShowSpecification,
    *,
    app: SkybrushServer,
    log: Logger,
    hooks: Iterable[ShowEncodingHook] | None = None,
) -> SkybrushBinaryShowFile:
    """Encodes a show specification into Skybrush binary format.

    Args:
        show: the show specification to encode.
        app: the Skybrush server instance, used to access extensions and APIs.
        log: a logger instance for logging messages during the encoding process.
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

    pyro_program: bytes | None = None
    rth_plan: bytes | None = None
    yaw_setpoints: bytes | None = None
    pro_keys = set(show.keys()).intersection(["pyro", "rthPlan", "yawControl"])
    if pro_keys:
        try:
            api = app.import_api("show_pro")
            if not api.loaded:
                raise RuntimeError(
                    f"Show pro extension is not loaded, neglecting {'and'.join(pro_keys)} from the show"
                )
        except NoSuchExtension:
            log.warning(
                f"Show pro extension is not available, neglecting {'and'.join(pro_keys)} from the show"
            )
        except RuntimeError as ex:
            log.warning(str(ex))
        else:
            pyro_program = api.encode_pyro(show)
            rth_plan = api.encode_rth_plan(show)
            yaw_setpoints = api.encode_yaw(show)

    async with SkybrushBinaryShowFile.create_in_memory() as show_file:
        await show_file.add_trajectory(trajectory)
        await show_file.add_encoded_light_program(light_program)
        if pyro_program:
            await show_file.add_encoded_event_list(pyro_program)
        if rth_plan:
            await show_file.add_encoded_rth_plan(rth_plan)
        if yaw_setpoints:
            await show_file.add_encoded_yaw_setpoints(yaw_setpoints)
        if hooks:
            for hook in hooks:
                await hook(show_file, show)
        await show_file.finalize()

    return show_file
