"""Connection for a MIDI port."""

from trio import to_thread

from .base import ConnectionBase, ReadableConnection
from .factory import create_connection

__all__ = ("MIDIPortConnection",)


@create_connection.register("midi")
class MIDIPortConnection(ConnectionBase, ReadableConnection["mido.Message"]):
    """Connection for a MIDI port."""

    def __init__(self, path):
        """Constructor.

        Parameters:
            path (str): name of the MIDI port to open. Call the ``list()``
                method of the ``MIDIPortConnection`` class to list all
                input and output ports.
        """
        # Lazy import used here so we don't bail out if the user does not
        # have mido but does not want to use MIDI ports either
        super(MIDIPortConnection, self).__init__()

        import mido

        self.mido = mido
        self.path = path
        self._port = None

    async def _close(self):
        """Closes the MIDI port connection."""
        await to_thread.run_sync(self._port.close)
        self._port = None

    async def _open(self):
        """Opens the MIDI port connection."""
        self._port = await to_thread.run_sync(self.mido.open_input, self.path)

    async def read(self):
        """Reads the next message from the MIDI port, blocking until one
        actually arrives.

        Returns:
            the next message from the MIDI port
        """
        # It would be much better to do this with a worker thread and queues,
        # but in order to spawn a worker thread, we would need to pass in a
        # nursery
        return await to_thread.run_sync(self._port.receive, cancellable=True)

    @staticmethod
    def list():
        """Returns a tuple containing the names of all MIDI input and
        output ports.

        Returns:
            (List[str], List[str]): names of all MIDI input and output
                ports
        """
        import mido

        return mido.get_input_names(), mido.get_output_names()


async def main():
    """Tester function that prints the names of all available input and
    output MIDI ports, then prints all incoming messages on the first
    input port.
    """
    input_ports, output_ports = MIDIPortConnection.list()
    print("Output ports:")
    print("\n".join(output_ports))
    print("")
    print("Input ports:")
    print("\n".join(input_ports))
    print("")

    if input_ports:
        async with MIDIPortConnection(input_ports[0]) as conn:
            while True:
                print(await conn.read())


if __name__ == "__main__":
    import trio

    trio.run(main)
