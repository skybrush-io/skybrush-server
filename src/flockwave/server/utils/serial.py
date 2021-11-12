from typing import Any, Dict, FrozenSet, Iterable, Optional, Tuple

__all__ = ("describe_serial_port", "list_serial_ports")


#: Type specification for dict-style serial port configurations
SerialPortConfiguration = Dict[str, Any]

#: Type specification for a generic serial port descriptor returned from
#: `list_serial_ports()`
SerialPortDescriptor = Any


def describe_serial_port(
    port: SerialPortDescriptor, use_hardware_id: bool = False
) -> str:
    """Returns a human-readable description for the given serial port that
    should be specific enough for a user to identify the port.

    Parameters:
        port: the port to describe; must be one of the objects returned from
            `list_serial_ports()`
        use_hardware_id: whether to use the `hwid` attribute provided by the
            serial port driver
    """
    description = port.description if port.description != "n/a" else ""
    hwid = (
        port.hwid if use_hardware_id and getattr(port, "hwid", "n/a") != "n/a" else ""
    )

    label = description or port.name or port.device
    if hwid:
        label = f"{label} ({hwid})"

    return label


def describe_serial_port_configuration(
    config: SerialPortConfiguration, only: Optional[Iterable[str]] = None
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


_RTK_BASE_BLACKLIST: FrozenSet[Tuple[int, int]] = frozenset(
    [
        (0x1209, 0x5740),  # ArduCopter generic
        (0x1209, 0x5741),  # Pixhawk1
        (0x2DAE, 0x1011),  # CubeBlack
        (0x2DAE, 0x1101),  # CubeBlack+
        (0x2DAE, 0x1012),  # CubeYellow
        (0x2DAE, 0x1015),  # CubePurple
        (0x2DAE, 0x1016),  # CubeOrange
        (0x3162, 0x004B),  # Holybro Durandal
        (0x26AC, 0x0011),  # Pixhawk1 bootloader
        (0x2DAE, 0x1001),  # CubeBlack bootloader
        (0x2DAE, 0x1002),  # CubeYellow bootloader
        (0x2DAE, 0x1005),  # CubePurple bootloader
    ]
)


def is_likely_not_rtk_base_station(desc: SerialPortDescriptor) -> bool:
    """Returns true if the serial port described by the given descriptor is very
    likely NOT an RTK base station.

    This function essentially matches the USB vendor and product ID from the
    port configuration against a list of known VID-PID pairs that occur
    frequently in the UAV community but are known not to be RTK base stations.
    Typical devices that are excluded this way are autopilots and bootloaders
    of autopilots.
    """
    vid, pid = getattr(desc, "vid", None), getattr(desc, "pid", None)
    if not isinstance(vid, int) or not isinstance(pid, int):
        return False

    return (vid, pid) in _RTK_BASE_BLACKLIST
