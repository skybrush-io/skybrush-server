"""Logging middleware that the show extension installs."""

from logging import Logger
from time import monotonic
from typing import Any, Optional

from flockwave.gps.vectors import FlatEarthToGPSCoordinateTransformation
from flockwave.server.model import Client, FlockwaveMessage

from .metadata import ShowMetadata

ShowFingerprint = list[Any]
"""Typing specification for the fingerprint of a show containing its most
basic parameters.
"""


def get(input: dict[str, Any], *args: str) -> Any:
    """Helper function to retrieve deeply nested items from a dict-of-dicts."""
    result: Any = input
    for arg in args:
        if isinstance(result, dict):
            result = result.get(arg)
        else:
            result = None
            break
    return result


class ShowUploadLoggingMiddleware:
    """Logging middleware that the show extension installs. It will print log
    messages whenever it detects that a new show upload has started.
    """

    _last_show_metadata: Optional[ShowMetadata] = None
    """The metadata of the last show upload that was seen by the
    middleware.
    """

    _last_show_upload_command_at: float
    """Timestamp when the last show upload command was detected."""

    _last_show_upload_fingerprint: Optional[ShowFingerprint] = None
    """Fingerprint containing the basic parameters of the last show upload.
    Used to decide whether it's a new show upload or most likely not.
    """

    _log: Logger
    """Logger that the middleware will write to."""

    def __init__(self, log: Logger):
        """Constructor.

        Parameter:
            log: logger that the middleware will write to
        """
        self._last_show_metadata = None
        self._last_show_upload_command_at = monotonic() - 1000
        self._log = log

    def __call__(self, message: FlockwaveMessage, sender: Client) -> FlockwaveMessage:
        show = self._extract_show(message)
        if show:
            now = monotonic()
            fingerprint = self._get_show_fingerprint(show)

            should_log = (
                fingerprint != self._last_show_upload_fingerprint
                or now - self._last_show_upload_command_at >= 30
            )
            if should_log:
                fmt_fingerprint = self._format_fingerprint(fingerprint)
                show_id = str(fingerprint[0]) if fingerprint[0] else ""
                sep = ", " if fmt_fingerprint else ""
                self._log.info(
                    f"Show upload started{sep}{fmt_fingerprint}", extra={"id": show_id}
                )

                self._last_show_metadata = self._get_metadata_from_upload_request(show)

            self._last_show_upload_command_at = now
            self._last_show_upload_fingerprint = fingerprint

        return message

    def _extract_show(self, message: FlockwaveMessage) -> Optional[dict[str, Any]]:
        """Checks whether the given message is a show upload and extracts the
        show specification out of the message if it is.
        """
        type = message.get_type()
        if type == "OBJ-CMD":
            cmd = message.body.get("command", "")
            if cmd == "__show_upload":
                kwds = message.body.get("kwds", {})
                if isinstance(kwds, dict) and "show" in kwds:
                    return kwds["show"]

    @property
    def last_show_metadata(self) -> Optional[ShowMetadata]:
        """Returns the metadata of the last show upload that was seen by the
        middleware.
        """
        return self._last_show_metadata

    @staticmethod
    def _get_show_fingerprint(show: dict[str, Any]) -> ShowFingerprint:
        """Extracts the basic show parameters like the origin and the orientation
        from the upload. These are used to decide whether an upload attempt is
        probably a continuation of an ongoing sequence of requests from the
        client or a completely new one.
        """
        return [
            get(show, "mission", "id"),
            get(show, "coordinateSystem"),
            get(show, "amslReference"),
        ]

    @staticmethod
    def _get_metadata_from_upload_request(show: dict[str, Any]) -> ShowMetadata:
        """Extracts the metadata of the current show being uploaded. This is
        returned to consumers of the API of the show extension when the caller
        requests the metadata of the last uploaded show.
        """
        coordinate_system = show.get("coordinateSystem")
        if not isinstance(coordinate_system, dict):
            coordinate_system = {}

        geofence = show.get("geofence")
        if not isinstance(geofence, dict):
            geofence = None

        mission = show.get("mission")
        if not isinstance(mission, dict):
            mission = {}

        maybe_amsl_reference = show.get("amslReference")
        if not isinstance(maybe_amsl_reference, (int, float)):
            maybe_amsl_reference = None

        return {
            "coordinateSystem": {
                "origin": coordinate_system.get("origin"),
                "orientation": str(coordinate_system.get("orientation", "")),
                "type": coordinate_system.get("type"),
            },
            "amslReference": maybe_amsl_reference,
            "geofence": geofence,
            "mission": {
                "id": str(mission.get("id", "")),
                "title": str(mission.get("title", "")),
                "numDrones": int(mission.get("numDrones", 0)),
            },
        }

    @staticmethod
    def _format_fingerprint(fingerprint: ShowFingerprint) -> str:
        """Returns a formatted representation of the show fingerprint for
        logging purposes.
        """
        parts = []
        if fingerprint[1] and isinstance(fingerprint[1], dict):
            try:
                xform = FlatEarthToGPSCoordinateTransformation.from_json(fingerprint[1])
            except (TypeError, RuntimeError):
                # TypeError may be raised if fingerprint[1]["origin"] is None,
                # which may be the case for indoor shows
                xform = None
            if xform:
                try:
                    parts.append(
                        f"{xform.orientation:.1f}° {xform.type.upper()} "
                        f"at {xform.origin.lat:.9g}° {xform.origin.lon:.9g}°"
                    )
                except Exception:
                    pass
        if fingerprint[2] is not None:
            try:
                parts.append(f"AMSL reference at {fingerprint[2]:.1f}m")
            except Exception:
                pass
        return ", ".join(parts)
