"""Show file builder class that manages the construction of a Skybrush binary
show file from a show specification and the contributions of the extensions
that participate in the show encoding process.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar, cast

from .formats import (
    ShowEvent,
    ShowMetadataTag,
    SkybrushBinaryFormatBlockType,
    SkybrushBinaryShowFile,
    TLVEncoder,
)

if TYPE_CHECKING:
    from types import TracebackType

    from .metadata import ShowMetadata
    from .trajectory import TrajectorySpecification

__all__ = ("ShowFileBuilder",)


class ShowFileBuilder:
    """Class that manages the construction of a Skybrush binary show file.

    The builder wraps an existing `SkybrushBinaryShowFile` object (or creates
    a new, in-memory one when constructed with the `create_in_memory()`
    class method) and it is handed to the encoding hooks that are registered
    by extensions during the show encoding process. Extensions may use the
    builder to append additional blocks to the show file being constructed,
    and they may also add events to a shared, deferred event list. The
    deferred event list is encoded into a single event list block, sorted by
    the timestamps of the events, when the builder is finalized.

    The builder can also be used as an asynchronous context manager; exiting
    the context manager ends the lifecycle of the builder. When the context
    is exited without an exception, the builder is finalized. When the context
    is exited with an exception, the builder is closed without finalizing it
    so that a partially built show file does not end up looking like a
    complete, properly finalized one. Exceptions are never suppressed.
    Attempting to modify or finalize a closed builder raises a `RuntimeError`.
    """

    _reserved_block_types: ClassVar[frozenset[int]] = frozenset(
        {
            SkybrushBinaryFormatBlockType.EVENT_LIST,
        }
    )
    """Block types that may not be added directly to the show file via
    `add_block()`; these blocks are managed by the builder itself or by the
    show encoding process.
    """

    _show_file: SkybrushBinaryShowFile
    """The show file being constructed."""

    _events: list[ShowEvent]
    """The events collected so far, not yet encoded into the show file."""

    _handled: set[str]
    """The keys of the show specification that were marked as handled by the
    encoding hooks so far."""

    _closed: bool
    """Whether the lifecycle of the builder has ended; a closed builder may
    neither be modified nor finalized."""

    def __init__(self, show_file: SkybrushBinaryShowFile):
        """Constructor.

        Parameters:
            show_file: the show file being constructed
        """
        self._show_file = show_file
        self._events = []
        self._handled = set()
        self._closed = False

    @classmethod
    def create_in_memory(cls, version: int = 2) -> ShowFileBuilder:
        """Creates a new show file builder that works on a new, in-memory
        Skybrush binary show file.

        Parameters:
            version: the version number of the binary show file to create

        Returns:
            the builder; it can be used as an asynchronous context manager,
            and the underlying show file can be retrieved with the `show_file`
            property after the context was exited. Note that the context
            manager of the underlying show file itself is _not_ entered as it
            is a no-op for in-memory show files; the underlying in-memory
            buffer remains readable after the builder was closed.
        """
        return cls(SkybrushBinaryShowFile.create_in_memory(version=version))

    async def __aenter__(self) -> ShowFileBuilder:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Ends the lifecycle of the builder when the context is exited.

        When the context is exited without an exception, the builder is
        finalized. When the context is exited with an exception, the builder
        is closed without finalizing it. Exceptions are never suppressed.
        """
        if not self._closed:
            if exc_type is None:
                await self.finalize()
            else:
                self._closed = True

        return False

    @property
    def handled(self) -> frozenset[str]:
        """The keys of the show specification that were marked as handled by
        the encoding hooks so far.
        """
        return frozenset(self._handled)

    @property
    def show_file(self) -> SkybrushBinaryShowFile:
        """The show file being constructed.

        While the builder is being used (typically by the encoding hooks),
        this is a low-level property that provides direct access to the
        underlying show file; it should be avoided if possible. Use the typed
        methods of the builder instead, which guard against common mistakes
        like adding multiple event list blocks to the show file.

        After the builder was closed, the property can be used to retrieve
        the resulting (finalized) show file.
        """
        return self._show_file

    async def add_block(self, type: int, body: bytes) -> None:
        """Adds a new block with the given type and body to the end of the
        show file being constructed.

        Parameters:
            type: the type of the block
            body: the body of the block, in encoded form

        Raises:
            ValueError: if the block type is reserved and cannot be added directly
            RuntimeError: if the builder is closed
        """
        self._check_not_closed()

        if type in self._reserved_block_types:
            raise ValueError(
                f"block type {type} is reserved and may not be added directly; "
                "use the dedicated methods of the builder instead"
            )

        await self._show_file.add_block(cast(SkybrushBinaryFormatBlockType, type), body)

    async def add_comment(self, comment: str | bytes, encoding: str = "utf-8") -> None:
        """Adds a new comment block to the end of the show file.

        Parameters:
            comment: the comment to add
            encoding: the encoding of the comment if it is a string; ignored
                when the comment is already a bytes object

        Raises:
            RuntimeError: if the builder is closed
        """
        self._check_not_closed()
        await self._show_file.add_comment(comment, encoding)

    async def add_encoded_gcs_light_control_setup(self, data: bytes) -> None:
        """Adds a new GCS light control setup block to the end of the show
        file.

        Parameters:
            data: the GCS light control setup to add, encoded in Skybrush
                format
        """
        await self.add_block(
            SkybrushBinaryFormatBlockType.GCS_LIGHT_CONTROL_SETUP, data
        )

    async def add_encoded_light_program(self, data: bytes) -> None:
        """Adds a new light program block to the end of the show file.

        Parameters:
            data: the light program to add, encoded in Skybrush format

        Raises:
            RuntimeError: if the builder is closed
        """
        self._check_not_closed()
        await self._show_file.add_encoded_light_program(data)

    async def add_encoded_rth_plan(self, data: bytes) -> None:
        """Adds a new return-to-home plan block to the end of the show file.

        Parameters:
            data: the RTH plan to add, encoded in Skybrush format
        """
        await self.add_block(SkybrushBinaryFormatBlockType.RTH_PLAN, data)

    async def add_encoded_yaw_setpoints(self, data: bytes) -> None:
        """Adds a new yaw control block to the end of the show file.

        Parameters:
            data: the yaw setpoints to add, encoded in Skybrush format
        """
        await self.add_block(SkybrushBinaryFormatBlockType.YAW_CONTROL, data)

    def add_event(
        self,
        *,
        timestamp: float,
        type: int,
        subtype: int,
        payload: bytes | int | float = b"\x00\x00\x00\x00",
    ) -> None:
        """Adds a new event to the shared, deferred event list of the builder.

        The event list is encoded into a single event list block, sorted by
        the timestamps of the events, when the builder is finalized. Events
        with identical timestamps keep the order in which they were added.

        Parameters:
            timestamp: the timestamp of the event, in seconds, relative to the
                start of the show; must be non-negative and small enough so
                that its representation in milliseconds fits into an unsigned
                32-bit integer
            type: the type of the event; an integer in the range 0-255
            subtype: the subtype of the event; an integer in the range 0-255
            payload: the payload of the event; either a binary blob of
                exactly four bytes, an unsigned 32-bit integer, or a
                single-precision float

        Raises:
            ValueError: if the type or the subtype is outside the range 0-255,
                or if the payload is not a valid four-byte binary blob, an
                unsigned 32-bit integer, or a single-precision float
            RuntimeError: if the builder is closed
        """
        self._check_not_closed()
        self._events.append(ShowEvent(timestamp, type, subtype, payload))

    def mark_handled(self, key: str) -> None:
        """Marks the given key of the show specification as handled.

        Encoding hooks should call this method for each key of the show
        specification that they have looked at and handled (even if the
        corresponding value turned out to be empty or not applicable) so that
        the show encoding process can warn the user about props of the show
        specification that no extension handled.

        Parameters:
            key: the key of the show specification to mark as handled

        Raises:
            RuntimeError: if the builder is closed
        """
        self._check_not_closed()
        self._handled.add(key)

    async def add_metadata(self, metadata: ShowMetadata) -> None:
        """Adds a new metadata block to the end of the show file.

        Parameters:
            metadata: the metadata to add
        """
        await self.add_tlv_block(
            SkybrushBinaryFormatBlockType.METADATA,
            [
                (ShowMetadataTag.UID, metadata.uid),
                (
                    ShowMetadataTag.DRONE_INDEX,
                    metadata.drone_index.to_bytes(2, "little", signed=False),
                ),
            ],
        )

    async def add_trajectory(self, trajectory: TrajectorySpecification) -> None:
        """Adds a new trajectory block to the end of the show file.

        Parameters:
            trajectory: the trajectory to add

        Raises:
            RuntimeError: if the builder is closed
        """
        self._check_not_closed()
        await self._show_file.add_trajectory(trajectory)

    async def add_tlv_block(
        self,
        type: int,
        entries: Iterable[tuple[int, bytes]],
    ) -> None:
        """Adds a new tag-length-value (TLV) block to the end of the show file.

        Parameters:
            type: the type of the block to add
            entries: an iterable of (tag, value) pairs to encode in the block
        """
        encoder = TLVEncoder()
        payload = encoder.encode_multiple_entries(entries)
        await self.add_block(cast(SkybrushBinaryFormatBlockType, type), payload)

    async def finalize(self) -> SkybrushBinaryShowFile:
        """Finalizes the show file being constructed.

        The shared, deferred event list of the builder is encoded into a
        single event list block, sorted by the timestamps of the events,
        appended to the end of the show file (if there are any events at
        all), and then the show file itself is finalized.

        Returns:
            the finalized show file

        Raises:
            RuntimeError: if the builder is closed
        """
        # Mark the builder as closed even before the finalization itself
        # succeeds; this ensures that a failed finalization attempt does not
        # leave the builder in a state where it could be modified or
        # finalized again
        self._check_not_closed()
        self._closed = True

        if self._events:
            events = sorted(self._events, key=lambda event: event.timestamp)
            payload = b"".join(event.encode() for event in events)
            await self._show_file.add_encoded_event_list(payload)

        await self._show_file.finalize()
        return self._show_file

    def _check_not_closed(self) -> None:
        """Checks whether the builder is closed and raises an error if so.

        Raises:
            RuntimeError: if the builder is closed
        """
        if self._closed:
            raise RuntimeError("the builder is closed")
