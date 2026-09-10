"""Extension that adds support for the ``SYS-...`` commands defined in the
Skybrush protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from trio import sleep_forever

from flockwave.server.ports import get_port_map
from flockwave.server.utils.system_time import (
    can_set_system_time_detailed_async,
    get_system_time_msec,
    set_system_time_msec_async,
)
from flockwave.server.version import __version__ as server_version

if TYPE_CHECKING:
    from logging import Logger

    from flockwave.server.app import SkybrushServer
    from flockwave.server.message_hub import MessageHandler, MessageHub
    from flockwave.server.model import Client
    from flockwave.server.model.messages import FlockwaveMessage

#############################################################################


def handle_SYS_PING(
    message: FlockwaveMessage, sender: Client, hub: MessageHub
) -> FlockwaveMessage:
    return hub.acknowledge(message)


def handle_SYS_PORTS(
    message: FlockwaveMessage, sender: Client, hub: MessageHub
) -> dict[str, Any]:
    return {"ports": dict(get_port_map())}


async def handle_SYS_TIME(
    message: FlockwaveMessage, sender: Client, hub: MessageHub
) -> dict[str, Any] | FlockwaveMessage:
    adjustment = message.body.get("adjustment")
    if adjustment is not None:
        adjustment = float(adjustment)
        allowed, reason = await can_set_system_time_detailed_async()
        if not allowed:
            return hub.acknowledge(
                message, outcome=False, reason=f"Permission denied. {reason}"
            )

        if adjustment != 0:
            # This branch is required so the client can test whether time
            # adjustments are supported by sending an adjustment with zero delta
            adjusted_time_msec = get_system_time_msec() + adjustment
            try:
                await set_system_time_msec_async(adjusted_time_msec)
            except Exception as ex:
                return hub.acknowledge(message, outcome=False, reason=str(ex))

    return {"timestamp": get_system_time_msec()}


def handle_SYS_VER(
    message: FlockwaveMessage, sender: Client, hub: MessageHub
) -> dict[str, str]:
    return {"software": "skybrushd", "version": server_version}


#############################################################################


async def run(
    app: SkybrushServer, configuration: dict[str, Any], logger: Logger
) -> None:
    """Runs the extension."""
    handlers: dict[str, MessageHandler] = {
        "SYS-PING": handle_SYS_PING,
        "SYS-PORTS": handle_SYS_PORTS,
        "SYS-TIME": handle_SYS_TIME,
        "SYS-VER": handle_SYS_VER,
    }

    with app.message_hub.use_message_handlers(handlers):
        await sleep_forever()


description = "Handlers for the basic system commands of the Skybrush protocol"
schema = {}
tags = ("system",)
