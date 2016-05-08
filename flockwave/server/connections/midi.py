"""Connection for a MIDI port."""

from __future__ import absolute_import

from .base import ConnectionBase, ConnectionState
from .factory import create_connection

__all__ = ("MIDIPortConnection", )


@create_connection.register("midi")
class MIDIPortConnection(ConnectionBase):
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
        import mido
        self.mido = mido
        self.path = path
        self._port = None

    def close(self):
        """Closes the MIDI port connection."""
        if self.state == ConnectionState.DISCONNECTED:
            return

        self._set_state(ConnectionState.DISCONNECTING)
        self._port.close()
        self._port = None
        self._set_state(ConnectionState.DISCONNECTED)

    def open(self):
        """Opens the MIDI port connection."""
        if self.state in (ConnectionState.CONNECTED,
                          ConnectionState.CONNECTING):
            return

        self._set_state(ConnectionState.CONNECTING)
        self._port = self.mido.open_input(self.path)
        self._set_state(ConnectionState.CONNECTED)

    def read(self):
        """Reads the next message from the MIDI port, blocking until one
        actually arrives.

        Returns:
            mido.Message: the next message from the MIDI port
        """
        return self._port.receive()

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


def main():
    """Tester function that prints the names of all available input and
    output MIDI ports.
    """
    input_ports, output_ports = MIDIPortConnection.list()
    print("Input ports:")
    print("\n".join(input_ports))
    print("")
    print("Output ports:")
    print("\n".join(output_ports))


if __name__ == "__main__":
    main()
