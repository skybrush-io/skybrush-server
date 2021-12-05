import sys
import time
import trio

from . import exceptions
from . import packet
from . import payload


class Socket(object):
    """An Engine.IO socket."""

    upgrade_protocols = ["websocket"]

    def __init__(self, server, sid):
        self.server = server
        self.sid = sid
        self.queue = self.server.create_queue()
        self.last_ping = time.monotonic()
        self.connected = False
        self.upgrading = False
        self.upgraded = False
        self.packet_backlog = []
        self.closing = False
        self.closed = False
        self.session = {}

    async def poll(self):
        """Wait for packets to send to the client."""
        packets = None
        with trio.move_on_after(self.server.ping_timeout):
            packets = [await self.queue.get()]
            self.queue.task_done()
        if packets is None:
            raise exceptions.QueueEmpty()
        if packets == [None]:
            return []
        try:
            packets.append(self.queue.get_nowait())
            self.queue.task_done()
        except trio.WouldBlock:
            pass
        return packets

    async def receive(self, pkt):
        """Receive packet from the client."""
        self.server.logger.info(
            "%s: Received packet %s data %s",
            self.sid,
            packet.packet_names[pkt.packet_type],
            pkt.data if not isinstance(pkt.data, bytes) else "<binary>",
        )
        if pkt.packet_type == packet.PING:
            self.last_ping = time.monotonic()
            await self.send(packet.Packet(packet.PONG, pkt.data))
        elif pkt.packet_type == packet.MESSAGE:
            await self.server._trigger_event(
                "message", self.sid, pkt.data, run_async=self.server.async_handlers
            )
        elif pkt.packet_type == packet.UPGRADE:
            await self.send(packet.Packet(packet.NOOP))
        elif pkt.packet_type == packet.CLOSE:
            await self.close(wait=False, abort=True)
        else:
            raise exceptions.UnknownPacketError()

    async def check_ping_timeout(self):
        """Make sure the client is still sending pings.

        This helps detect disconnections for long-polling clients.
        """
        if self.closed:
            raise exceptions.SocketIsClosedError()
        if time.monotonic() - self.last_ping > self.server.ping_interval + 5:
            self.server.logger.info("%s: Client is gone, closing socket", self.sid)
            # Passing abort=False here will cause close() to write a
            # CLOSE packet. This has the effect of updating half-open sockets
            # to their correct state of disconnected
            await self.close(wait=False, abort=False)
            return False
        return True

    async def send(self, pkt):
        """Send a packet to the client."""
        if not await self.check_ping_timeout():
            return
        if self.upgrading:
            self.packet_backlog.append(pkt)
        else:
            await self.queue.put(pkt)
        self.server.logger.info(
            "%s: Sending packet %s data %s",
            self.sid,
            packet.packet_names[pkt.packet_type],
            pkt.data if not isinstance(pkt.data, bytes) else "<binary>",
        )

    async def handle_get_request(self, environ):
        """Handle a long-polling GET request from the client."""
        connections = [
            s.strip() for s in environ.get("HTTP_CONNECTION", "").lower().split(",")
        ]
        transport = environ.get("HTTP_UPGRADE", "").lower()
        if "upgrade" in connections and transport in self.upgrade_protocols:
            self.server.logger.info(
                "%s: Received request to upgrade to %s", self.sid, transport
            )
            return await getattr(self, "_upgrade_" + transport)(environ)
        try:
            packets = await self.poll()
        except exceptions.QueueEmpty:
            exc = sys.exc_info()
            await self.close(wait=False)
            raise exc[0].with_traceback(exc[1], exc[2])
        return packets

    async def handle_post_request(self, environ):
        """Handle a long-polling POST request from the client."""
        length = int(environ.get("CONTENT_LENGTH", "0"))
        if length > self.server.max_http_buffer_size:
            raise exceptions.ContentTooLongError()
        else:
            body = await environ["wsgi.input"].read(length)
            p = payload.Payload(encoded_payload=body)
            for pkt in p.packets:
                await self.receive(pkt)

    async def close(self, wait=True, abort=False):
        """Close the socket connection."""
        if not self.closed and not self.closing:
            self.closing = True
            await self.server._trigger_event("disconnect", self.sid)
            if not abort:
                await self.send(packet.Packet(packet.CLOSE))
            self.closed = True
            if wait:
                await self.queue.join()

    async def _upgrade_websocket(self, environ):
        """Upgrade the connection from polling to websocket."""
        if self.upgraded:
            raise IOError("Socket has been upgraded already")
        if self.server._async["websocket"] is None:
            # the selected async mode does not support websocket
            return self.server._bad_request()
        ws = self.server._async["websocket"](self._websocket_handler)
        return await ws(environ)

    async def _websocket_handler(self, ws):
        """Engine.IO handler for websocket transport."""
        if self.connected:
            # the socket was already connected, so this is an upgrade
            self.upgrading = True  # hold packet sends during the upgrade

            try:
                pkt = await ws.wait()
            except IOError:  # pragma: no cover
                return
            decoded_pkt = packet.Packet(encoded_packet=pkt)
            if decoded_pkt.packet_type != packet.PING or decoded_pkt.data != "probe":
                self.server.logger.info(
                    "%s: Failed websocket upgrade, no PING packet", self.sid
                )
                return
            await ws.send(
                packet.Packet(packet.PONG, data=str("probe")).encode(always_bytes=False)
            )
            await self.queue.put(packet.Packet(packet.NOOP))  # end poll

            try:
                pkt = await ws.wait()
            except IOError:  # pragma: no cover
                return
            decoded_pkt = packet.Packet(encoded_packet=pkt)
            if decoded_pkt.packet_type != packet.UPGRADE:
                self.upgraded = False
                self.server.logger.info(
                    (
                        "%s: Failed websocket upgrade, expected UPGRADE packet, "
                        "received %s instead."
                    ),
                    self.sid,
                    pkt,
                )
                return
            self.upgraded = True

            # flush any packets that were sent during the upgrade
            for pkt in self.packet_backlog:
                await self.queue.put(pkt)
            self.packet_backlog = []
            self.upgrading = False
        else:
            self.connected = True
            self.upgraded = True

        # start separate writer thread
        async def writer():
            while True:
                packets = None
                try:
                    packets = await self.poll()
                except exceptions.QueueEmpty:
                    break
                if not packets:
                    # empty packet list returned -> connection closed
                    break
                try:
                    for pkt in packets:
                        await ws.send(pkt.encode(always_bytes=False))
                except Exception:
                    break

        async with trio.open_nursery() as nursery:
            nursery.start_soon(writer)

            self.server.logger.info("%s: Upgrade to websocket successful", self.sid)

            while True:
                p = None
                try:
                    with trio.move_on_after(self.server.ping_timeout):
                        p = await ws.wait()
                except IOError:
                    p = None
                if p is None:
                    # connection closed by client
                    break
                if isinstance(p, str):  # pragma: no cover
                    p = p.encode("utf-8")
                pkt = packet.Packet(encoded_packet=p)
                try:
                    await self.receive(pkt)
                except exceptions.UnknownPacketError:  # pragma: no cover
                    pass
                except exceptions.SocketIsClosedError:  # pragma: no cover
                    self.server.logger.info("Receive error -- socket is closed")
                    break
                except Exception:  # pragma: no cover
                    # if we get an unexpected exception we log the error and exit
                    # the connection properly
                    self.server.logger.exception("Unknown receive error")

            await self.queue.put(None)  # unlock the writer task so it can exit
        await self.close(wait=False, abort=True)
