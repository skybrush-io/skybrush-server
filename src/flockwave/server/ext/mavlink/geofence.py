"""Geofence-related data structures and functions for the MAVLink protocol."""

from enum import IntFlag
from functools import partial
from trio import fail_after, TooSlowError
from typing import Optional

from flockwave.server.model.geofence import (
    GeofenceCircle,
    GeofencePolygon,
    GeofenceStatus,
)

from .enums import MAVCommand, MAVMissionType
from .types import (
    MAVLinkMessage,
    MAVLinkMessageSpecification,
    MAVLinkMessageMatcher,
    spec,
)
from .utils import mavlink_nav_command_to_gps_coordinate

__all__ = (
    "GeofenceManager",
    "GeofenceType",
)


class GeofenceType(IntFlag):
    """Supported MAVLink-specific geofence types."""

    OFF = 0
    ALTITUDE = 1
    CIRCLE = 2
    POLYGON = 4
    ALL = ALTITUDE | CIRCLE | POLYGON


class GeofenceManager:
    """Class responsible for retrieving and setting geofence settings on a
    MAVLink connection.
    """

    @classmethod
    def for_uav(cls, uav):
        """Constructs a MAVFTP connection object to the given UAV."""
        sender = partial(uav.driver.send_packet, target=uav)
        return cls(sender)

    def __init__(self, sender):
        """Constructor.

        Parameters:
            sender: a function that can be called to send a MAVLink message and
                wait for an appropriate reply
        """
        self._sender = sender

    async def get_geofence_areas(
        self, status: Optional[GeofenceStatus] = None
    ) -> GeofenceStatus:
        """Returns the configured areas of the geofence from the MAVLink
        connection.

        Parameters:
            status: an optional input status object to update

        Returns:
            a GeofenceStatus object where the `polygons` and `circles` attributes
            will be filled appropriately with the retrieved information. All
            the other attributes will be left intact.
        """
        if status:
            status.clear_areas()
        else:
            status = GeofenceStatus()

        # Retrieve geofence polygons and circles
        mission_type = MAVMissionType.FENCE
        reply = await self._send_and_wait(
            spec.mission_request_list(mission_type=mission_type),
            spec.mission_count(mission_type=mission_type),
        )

        def add_polygon_to_result(poly):
            if poly and len(poly["points"]) == poly["count"]:
                status.polygons.append(
                    GeofencePolygon(
                        points=poly["points"], is_inclusion=poly["is_inclusion"]
                    )
                )

        to_point = mavlink_nav_command_to_gps_coordinate

        # Iterate over the mission items
        current_polygon = None  # Status of current polygon
        for index in range(reply.count):
            reply = await self._send_and_wait(
                spec.mission_request_int(seq=index, mission_type=mission_type),
                spec.mission_item_int(seq=index, mission_type=mission_type),
            )

            if reply.command in (
                MAVCommand.NAV_FENCE_POLYGON_VERTEX_INCLUSION,
                MAVCommand.NAV_FENCE_POLYGON_VERTEX_EXCLUSION,
            ):
                point_count = int(reply.param1)
                is_inclusion = (
                    reply.command == MAVCommand.NAV_FENCE_POLYGON_VERTEX_INCLUSION
                )
                starts_new_polygon = (
                    current_polygon is None
                    or not current_polygon["is_inclusion"] == is_inclusion
                    or current_polygon["count"] != point_count
                )
                if starts_new_polygon:
                    add_polygon_to_result(current_polygon)
                    current_polygon = {
                        "is_inclusion": is_inclusion,
                        "points": [],
                        "count": point_count,
                    }
                current_polygon["points"].append(to_point(reply))

            elif reply.command in (
                MAVCommand.NAV_FENCE_CIRCLE_INCLUSION,
                MAVCommand.NAV_FENCE_CIRCLE_EXCLUSION,
            ):
                status.circles.append(
                    GeofenceCircle(
                        center=to_point(reply),
                        radius=reply.param1,
                        is_inclusion=reply.command
                        == MAVCommand.NAV_FENCE_CIRCLE_INCLUSION,
                    )
                )

        # Make sure that the last polygon is also added
        add_polygon_to_result(current_polygon)

        # Return the assembled status
        return status

    async def get_geofence_rally_points(
        self, status: Optional[GeofenceStatus] = None
    ) -> GeofenceStatus:
        """Returns the configured rally points of the geofence from the MAVLink
        connection.

        Parameters:
            status: an optional input status object to update

        Returns:
            a GeofenceStatus object where the `rally_points` attribute will be
            filled appropriately with the retrieved information. All the other
            attributes will be left intact.
        """
        if status:
            status.clear_rally_points()
        else:
            status = GeofenceStatus()

        # Retrieve geofence rally points
        mission_type = MAVMissionType.RALLY
        reply = await self._send_and_wait(
            spec.mission_request_list(mission_type=mission_type),
            spec.mission_count(mission_type=mission_type),
        )

        # Iterate over the mission items
        for index in range(reply.count):
            reply = await self._send_and_wait(
                spec.mission_request_int(seq=index, mission_type=mission_type),
                spec.mission_item_int(seq=index, mission_type=mission_type),
            )

            if reply.command == MAVCommand.NAV_RALLY_POINT:
                status.rally_points.append(mavlink_nav_command_to_gps_coordinate(reply))

        # Return the assembled status
        return status

    async def get_geofence_areas_and_rally_points(
        self, status: Optional[GeofenceStatus] = None
    ) -> GeofenceStatus:
        """Returns the areas and rally points of the geofence from the MAVLink
        connection.

        Parameters:
            status: an optional input status object to update

        Returns:
            a GeofenceSatus object with updated area and rally point information
        """
        status = status or GeofenceStatus()
        await self.get_geofence_areas(status)
        await self.get_geofence_rally_points(status)
        return status

    async def _send_and_wait(
        self,
        message: MAVLinkMessageSpecification,
        expected_reply: MAVLinkMessageMatcher,
        *,
        timeout: float = 1.5,
        retries: int = 5,
    ) -> MAVLinkMessage:
        """Sends a message according to the given MAVLink message specification
        to the drone and waits for an expected reply, re-sending the message
        as needed a given number of times before timing out.

        Parameters:
            message: specification of the message to send
            expected_reply: message matcher that matches messages that we expect
                from the connection as a reply to the original message
            timeout: maximum number of seconds to wait before attempting to
                re-send the message
            retries: maximum number of retries before giving up

        Returns:
            the MAVLink message sent by the UAV in response

        Raises:
            TooSlowError: if the UAV failed to respond in time
        """
        while True:
            try:
                with fail_after(timeout):
                    return await self._sender(message, wait_for_response=expected_reply)
            except TooSlowError:
                if retries > 0:
                    retries -= 1
                    continue
                else:
                    raise
