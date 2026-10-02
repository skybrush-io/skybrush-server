from __future__ import annotations

from logging import Logger
from typing import TYPE_CHECKING

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


__all__ = ("encode_show",)


async def encode_show(
    show: ShowSpecification, app: SkybrushServer, log: Logger
) -> SkybrushBinaryShowFile:
    """Encodes a show specification into Skybrush binary format.

    Args:
        spec: the show specification to encode.

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
        # TODO(ntamas): call any additional hooks
        await show_file.finalize()

    return show_file
