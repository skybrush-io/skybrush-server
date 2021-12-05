import inspect
import trio

from .base_manager import BaseManager


class TrioManager(BaseManager):
    """Manage a client list for a Trio server."""

    async def can_disconnect(self, sid, namespace):
        return self.is_connected(sid, namespace)

    async def emit(
        self, event, data, namespace, room=None, skip_sid=None, callback=None, **kwargs
    ):
        """Emit a message to a single client, a room, or all the clients
        connected to the namespace.

        Note: this method is a coroutine.
        """
        if namespace not in self.rooms or room not in self.rooms[namespace]:
            return
        tasks = []
        if not isinstance(skip_sid, list):
            skip_sid = [skip_sid]
        for sid, eio_sid in self.get_participants(namespace, room):
            if sid not in skip_sid:
                if callback is not None:
                    id = self._generate_ack_id(sid, callback)
                else:
                    id = None
                tasks.append(
                    (self.server._emit_internal, (eio_sid, event, data, namespace, id))
                )
        if not tasks:  # pragma: no cover
            return
        async with trio.open_nursery() as nursery:
            for func, args in tasks:
                nursery.start_soon(func, *args)

    async def close_room(self, room, namespace):
        """Remove all participants from a room.

        Note: this method is a coroutine.
        """
        return super().close_room(room, namespace)

    async def trigger_callback(self, sid, id, data):
        """Invoke an application callback.

        Note: this method is a coroutine.
        """
        callback = None
        try:
            callback = self.callbacks[sid][id]
        except KeyError:
            # if we get an unknown callback we just ignore it
            self._get_logger().warning("Unknown callback received, ignoring.")
        else:
            del self.callbacks[sid][id]
        if callback is not None:
            ret = callback(*data)
            if inspect.iscoroutine(ret):
                with trio.CancelScope():
                    await ret
