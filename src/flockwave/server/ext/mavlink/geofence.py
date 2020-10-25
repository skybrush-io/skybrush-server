"""Geofence-related data structures and functions for the MAVLink protocol."""

from enum import IntFlag
from functools import partial, singledispatch
from trio import fail_after, TooSlowError
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from flockwave.logger import Logger
from flockwave.server.model.geofence import (
    GeofenceCircle,
    GeofencePolygon,
    GeofenceStatus,
)

from .enums import MAVCommand, MAVFrame, MAVMissionResult, MAVMissionType
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
        log = uav.driver.log
        return cls(sender, log=log)

    def __init__(self, sender, log: Optional[Logger] = None):
        """Constructor.

        Parameters:
            sender: a function that can be called to send a MAVLink message and
                wait for an appropriate reply
            log: optional logger to use for logging messages
        """
        self._sender = sender
        self._log = log

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
            mission_type,
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
                mission_type,
                spec.mission_request_int(seq=index, mission_type=mission_type),
                spec.mission_item_int(seq=index, mission_type=mission_type),
                timeout=0.25,
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

        # Send final acknowledgment
        await self._send_final_ack(mission_type)

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
            mission_type,
            spec.mission_request_list(mission_type=mission_type),
            spec.mission_count(mission_type=mission_type),
        )

        # Iterate over the mission items
        for index in range(reply.count):
            reply = await self._send_and_wait(
                mission_type,
                spec.mission_request_int(seq=index, mission_type=mission_type),
                spec.mission_item_int(seq=index, mission_type=mission_type),
                timeout=0.25,
            )

            if reply.command == MAVCommand.NAV_RALLY_POINT:
                status.rally_points.append(mavlink_nav_command_to_gps_coordinate(reply))

        # Send final acknowledgment
        await self._send_final_ack(mission_type)

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

    async def set_geofence_areas(
        self,
        areas: Optional[Iterable[Union[GeofenceCircle, GeofencePolygon]]] = None,
    ) -> None:
        """Uploads the given geofence polygons and circles to the MAVLink
        connection.

        Parameters:
            areas: the polygons and circles to upload

        Raises:
            TooSlowError: if the UAV failed to respond in time
        """
        items = []
        for area in areas:
            items.extend(_convert_area_to_mission_items(area))
        if not items:
            return

        num_items = len(items)
        mission_type = MAVMissionType.FENCE

        index, finished = None, False
        while not finished:
            if index is None:
                # We need to let the drone know how many items there will be
                message = spec.mission_count(count=num_items, mission_type=mission_type)
                should_resend = True
            else:
                # We need to send the item with the given index to the drone
                command, kwds = items[index]
                params = dict(
                    seq=index,
                    command=command,
                    mission_type=mission_type,
                    param1=0,
                    param2=0,
                    param3=0,
                    param4=0,
                    x=0,
                    y=0,
                    z=0,
                    frame=MAVFrame.GLOBAL,
                    current=0,
                    autocontinue=0,
                )
                params.update(kwds)
                message = spec.mission_item_int(**params)
                should_resend = False

            # Drone must respond with requesting the next item (or asking
            # to repeat the current one), or by sending an ACK or NAK. We should
            # _not_ attempt to re-send geofence items; it is the responsiblity
            # of the drone to request them again if they got lost.
            #
            # TODO(ntamas): we could also receive MISSION_REQUEST_INT here,
            # we need to handle both!
            expected_reply = spec.mission_request(mission_type=mission_type)

            # We have different policies for the initial message that
            # initiates the upload and the subsequent messages that are
            # responding to the requests from the drone.
            #
            # For the initial message, we attempt to re-send it in case it
            # got lost. For subsequent messages, we never re-send it (it is
            # the responsibility of the drone to request them again if our
            # reply got lost), but we assume that the upload timed out if
            # we haven't received an ACK or the next request from the drone
            # in five seconds.
            reply = await self._send_and_wait(
                mission_type,
                message,
                expected_reply,
                timeout=1.5 if should_resend else 5,
                retries=5 if should_resend else 0,
            )
            if reply is None:
                # Final ACK received
                finished = True
            else:
                # Drone requested another item
                index = reply.seq

    async def _send_and_wait(
        self,
        mission_type: MAVMissionType,
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
            mission_type: type of the mission we are dealing with
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
        # For each mission-related message that we send, we could receive either
        # the expected response or a MISSION_ACK with an error code.
        if expected_reply[0] == "MISSION_ACK":
            replies = {"ack": expected_reply}
        else:
            replies = {
                "response": expected_reply,
                "ack": spec.mission_ack(mission_type=mission_type),
            }

        while True:
            try:
                with fail_after(timeout):
                    key, response = await self._sender(message, wait_for_one_of=replies)

                    if key == "response":
                        # Got the response that we expected
                        return response
                    else:
                        # Got an ACK. Check whether it has an error code.
                        if response.type == MAVMissionResult.ACCEPTED:
                            return None
                        else:
                            raise RuntimeError(
                                f"MAVLink mission operation returned code {response.type}"
                            )

            except TooSlowError:
                if retries > 0:
                    retries -= 1
                    continue
                else:
                    raise TooSlowError("MAVLink mission operation timed out") from None

    async def _send_final_ack(self, mission_type: int) -> None:
        """Sends the final acknowledgment at the end of a mission download
        transaction.
        """
        try:
            await self._sender(spec.mission_ack(mission_type=mission_type))
        except Exception as ex:
            # doesn't matter, we got what we needed
            print(repr(ex))


@singledispatch
def _convert_area_to_mission_items(area: Any) -> List[Tuple[int, Dict]]:
    raise ValueError(f"Unknown geofence area type: {type(area)!r}")


@_convert_area_to_mission_items.register
def _(area: GeofenceCircle) -> List[Tuple[int, Dict]]:
    return [
        (
            (
                MAVCommand.NAV_FENCE_CIRCLE_INCLUSION
                if area.is_inclusion
                else MAVCommand.NAV_FENCE_CIRCLE_EXCLUSION
            ),
            {
                "param1": area.radius,
                "x": int(area.center.lat * 1e7),
                "y": int(area.center.lon * 1e7),
            },
        )
    ]


@_convert_area_to_mission_items.register
def _(area: GeofencePolygon) -> List[Tuple[int, Dict]]:
    points = list(area.points)
    if points and points[0] == points[-1]:
        points.pop()

    num_points = len(points)

    return [
        (
            (
                MAVCommand.NAV_FENCE_POLYGON_VERTEX_INCLUSION
                if area.is_inclusion
                else MAVCommand.NAV_FENCE_POLYGON_VERTEX_EXCLUSION
            ),
            {
                "param1": num_points,
                "x": int(point.lat * 1e7),
                "y": int(point.lon * 1e7),
            },
        )
        for point in points
    ]
