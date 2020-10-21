from typing import Any, Dict, Iterable, Optional

__all__ = ("describe_serial_port", "list_serial_ports")


#: Type specification for a generic serial port descriptor returned from
#: `list_serial_ports()`
SerialPortDescriptor = Any


def describe_serial_port(port: SerialPortDescriptor) -> str:
    """Returns a human-readable description for the given serial port that
    should be specific enough for a user to identify the port.

    Parameters:
        port: the port to describe; must be one of the objects returned from
            `list_serial_ports()`
    """
    description = port.description if port.description != "n/a" else ""
    hwid = port.hwid if port.hwid != "n/a" else ""

    label = description or port.name or port.device
    if hwid:
        label = f"{label} ({hwid})"

    return label


def describe_serial_port_configuration(
    config: Dict[str, Any], only: Optional[Iterable[str]] = None
) -> str:
    """Returns a human-readable description of the given serial port configuration
    object. The object must have the same keyword arguments as the ones supported
    in `flockwave.connections.serial.SerialPortConnection`.

    Parameters:
        config: the configuration object
        only: when specified, only the keys in this iterable will be considered
            from the configuration object
    """
    if only is not None:
        config = {k: config[k] for k in only if k in config}

    parts = []

    value = config.get("path")
    if value is not None:
        parts.append(str(value))

    vid = config.get("vid")
    pid = config.get("pid")
    if vid and pid:
        parts.append(f"USB {vid}:{pid}")
    elif vid:
        parts.append(f"USB vendor ID {vid}")
    elif pid:
        parts.append(f"USB product ID {pid}")

    value = config.get("manufacturer")
    if value is not None:
        parts.append(f"manufacturer: {value}")

    value = config.get("product")
    if value is not None:
        parts.append(f"product: {value}")

    value = config.get("serial_number")
    if value is not None:
        parts.append(f"serial: {value}")

    value = config.get("baud")
    if value is not None:
        parts.append(f"{value} baud")

    value = config.get("stopbits")
    if value is not None:
        if value == 1:
            parts.append(f"{value} stop bit")
        else:
            parts.append(f"{value} stop bits")

    return ", ".join(parts).capitalize()


def list_serial_ports() -> Iterable[SerialPortDescriptor]:
    """Enumerates all serial ports and USB-to-serial interfaces on the computer
    and returns an iterable that can be used to iterate over them.
    """
    from serial.tools.list_ports import comports

    return comports()
