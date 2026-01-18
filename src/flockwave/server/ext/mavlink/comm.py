"""Communication manager that facilitates communication between a MAVLink-based
UAV and the ground station via some communication link.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from compose import compose

from flockwave.channels import (
    create_lossy_channel,
)
from flockwave.connections import Connection
from flockwave.networking import format_socket_address
from flockwave.server.comm import CommunicationManager

from .channel import create_mavlink_message_channel
from .signing import MAVLinkSigningConfiguration
from .types import MAVLinkMessageSpecification

__all__ = ("create_communication_manager",)


def format_mavlink_channel_address(address: Any) -> str:
    """Returns a formatted representation of the address of a MAVLink message
    channel.
    """
    try:
        return format_socket_address(address)
    except ValueError:
        return str(address)


def create_communication_manager(
    *,
    packet_loss: float = 0,
    network_id: str = "",
    system_id: int = 255,
    signing: MAVLinkSigningConfiguration = MAVLinkSigningConfiguration.DISABLED,
    use_broadcast_rate_limiting: bool = False,
) -> CommunicationManager[MAVLinkMessageSpecification, Any]:
    """Creates a communication manager instance for a single network managed
    by the extension.

    Parameters:
        packet_loss: simulated packet loss probability; zero means normal
            behaviour
        system_id: the system ID to use in MAVLink messages sent by this
            communication manager
        signing: specifies how to handle signed MAVLink messages in both the
            incoming and the outbound direction
        use_broadcast_rate_limiting: whether to apply a small delay after
            sending each broadcast packet; this can be used to counteract
            rate limiting problems if there are any. Typically you can leave
            this setting at `False` unless you see lots of lost broadcast
            packets.
    """
    # Create a dictionary to cache link IDs to existing connections so we can
    # keep on using the same link ID for the same connection even if it is
    # closed and re-opened later
    link_ids: dict[Connection, int] = {}
    channel_factory = partial(
        create_mavlink_message_channel,
        signing=signing,
        link_ids=link_ids,
        network_id=network_id,
        system_id=system_id,
    )

    if packet_loss > 0:
        channel_factory = compose(
            partial(create_lossy_channel, loss_probability=packet_loss), channel_factory
        )

    manager = CommunicationManager(
        channel_factory=channel_factory,
        format_address=format_mavlink_channel_address,
    )

    if use_broadcast_rate_limiting:
        manager.broadcast_delay = 0.005

    return manager
