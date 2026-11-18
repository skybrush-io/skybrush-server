"""Helpers for tests that run the `crazyflie` extension together with the
`lps` extension.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from trio import open_nursery
from trio.testing import wait_all_tasks_blocked

from flockwave.server.ext.crazyflie.driver import CrazyflieDriver
from flockwave.server.ext.crazyflie.extension import CrazyflieDronesExtension
from flockwave.server.ext.lps.extension import LocalPositioningSystemsExtension
from flockwave.server.registries import ObjectRegistry

from .recording_message_hub import RecordingMessageHub

__all__ = ("running_crazyflie_extension",)


@asynccontextmanager
async def running_crazyflie_extension(
    hub: RecordingMessageHub, data_dir: Path, positioning: str
) -> AsyncGenerator[
    tuple[CrazyflieDronesExtension, LocalPositioningSystemsExtension, CrazyflieDriver]
]:
    """Runs the Crazyflie extension with no radio connections, together with
    the `lps` extension, until the context is exited.
    """
    object_registry = ObjectRegistry()
    lps_ext = LocalPositioningSystemsExtension()
    lps_ext.app = SimpleNamespace(  # type: ignore
        command_execution_manager=MagicMock(),
        message_hub=hub,
        object_registry=object_registry,
    )

    # The same mock is returned for each API to let the tests inspect it
    apis: dict[str, Any] = {"lps": SimpleNamespace(**lps_ext.exports())}

    ext = CrazyflieDronesExtension()
    ext.log = MagicMock()
    ext.app = SimpleNamespace(  # type: ignore
        dirs=SimpleNamespace(user_cache_path=data_dir, user_data_path=data_dir),
        object_registry=object_registry,
        uav_driver_registry=MagicMock(),
        import_api=lambda name, *args: apis.setdefault(name, MagicMock()),
    )
    driver = ext._driver
    assert driver is not None
    configuration = {"connections": [], "positioning": positioning}
    ext.configure_driver(driver, configuration)

    async with open_nursery() as nursery:
        nursery.start_soon(lps_ext.run)
        await wait_all_tasks_blocked()

        async with open_nursery() as cf_nursery:
            cf_nursery.start_soon(ext._run, ext.app, configuration)
            await wait_all_tasks_blocked()
            yield ext, lps_ext, driver
            cf_nursery.cancel_scope.cancel()

        assert list(lps_ext._lps_registry.ids) == []
        assert list(lps_ext._lps_type_registry.ids) == []
        assert driver.lighthouse is None
        assert driver.loco is None

        nursery.cancel_scope.cancel()
