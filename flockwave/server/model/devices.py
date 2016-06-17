"""Device and channel-related model classes."""

from __future__ import absolute_import

from flockwave.spec.schema import get_enum_from_schema, \
    get_complex_object_schema
from six import add_metaclass
from .metamagic import ModelMeta

__all__ = ("ChannelNode", "ChannelOperation", "ChannelType", "DeviceClass",
           "DeviceTree", "DeviceNode", "DeviceTreeNodeType",
           "UAVNode")

ChannelOperation = get_enum_from_schema("channelOperation",
                                        "ChannelOperation")
ChannelType = get_enum_from_schema("channelType", "ChannelType")
DeviceClass = get_enum_from_schema("deviceClass", "DeviceClass")
DeviceTreeNodeType = get_enum_from_schema("deviceTreeNodeType",
                                          "DeviceTreeNodeType")

_channel_type_mapping = {
    int: "number",
    float: "number",
    str: "string",
    bool: "boolean",
    object: "object"
}


def _channel_type_from_object(cls, obj):
    """Converts a Python type object to a corresponding channel type
    object. Also accepts ChannelType objects as input, in which case
    the object is returned as is.

    Parameters:
        obj (Union[ChannelType, type]): the type object to convert to
            a ChannelType

    Returns:
        ChannelType: the appropriate channel type corresponding to the
            Python type
    """
    if isinstance(obj, ChannelType):
        return obj
    else:
        try:
            name = _channel_type_mapping[obj]
        except KeyError:
            raise TypeError("{0!r} cannot be converted to a "
                            "ChannelType".format(obj))
        return cls[name]

ChannelType.from_object = classmethod(_channel_type_from_object)


@add_metaclass(ModelMeta)
class DeviceTreeNodeBase(object):
    """Class representing a single node in a Flockwave device tree."""

    class __meta__:
        schema = get_complex_object_schema("deviceTreeNode")

    def _add_child(self, id, node):
        """Adds the given node as a child node to this node.

        Parameters:
            id (str): the ID of the node
            node (DeviceTreeNodeBase): the node to add

        Returns:
            DeviceTreeNodeBase: the node that was added
        """
        if not hasattr(self, "children"):
            self.children = {}
        if id in self.children:
            raise ValueError("another child node already exists with "
                             "ID={0!r}".format(id))
        self.children[id] = node
        return node


class ChannelNode(DeviceTreeNodeBase):
    """Class representing a device node in a Flockwave device tree."""

    def __init__(self, channel_type, operations=None):
        """Constructor.

        Parameters:
            channel_type (ChannelType): the type of the channel
            operations (List[ChannelOperation]): the allowed operations of
                the channel. Defaults to ``[ChannelOperation.read]`` if
                set to ``None``.
        """
        super(ChannelNode, self).__init__()

        if operations is None:
            operations = [ChannelOperation.read]

        self.type = DeviceTreeNodeType.channel
        self.subtype = channel_type
        self.operations = list(operations)

    @property
    def subtype(self):
        """Alias to ``subType``."""
        return self.subType

    @subtype.setter
    def subtype(self, value):
        self.subType = value


class DeviceNode(DeviceTreeNodeBase):
    """Class representing a device node in a Flockwave device tree."""

    def __init__(self, device_class=DeviceClass.misc):
        """Constructor."""
        super(DeviceNode, self).__init__()
        self.type = DeviceTreeNodeType.device
        self.device_class = device_class

    def add_channel(self, id, type):
        """Adds a new channel with the given identifier to this device
        node.

        Parameters:
            id (str): the identifier of the channel being added.
            type (ChannelType): the type of the channel

        Returns:
            ChannelNode: the channel node that was added.
        """
        channel_type = ChannelType.from_object(type)
        return self._add_child(id, ChannelNode(channel_type=channel_type))

    def add_device(self, id):
        """Adds a new device with the given identifier as a sub-device
        to this device node.

        Parameters:
            id (str): the identifier of the device being added.

        Returns:
            DeviceNode: the device tree node that was added.
        """
        return self._add_child(id, DeviceNode())

    @property
    def device_class(self):
        """Alias to ``deviceClass``."""
        return self.deviceClass

    @device_class.setter
    def device_class(self, value):
        self.deviceClass = value


class UAVNode(DeviceTreeNodeBase):
    """Class representing a UAV node in a Flockwave device tree."""

    def __init__(self):
        """Constructor."""
        super(UAVNode, self).__init__()
        self.type = DeviceTreeNodeType.uav

    def add_device(self, id):
        """Adds a new device with the given identifier to this UAV node.

        Parameters:
            id (str): the identifier of the device being added.

        Returns:
            DeviceNode: the device tree node that was added.
        """
        return self._add_child(id, DeviceNode())


class DeviceTree(object):
    """A device tree of a UAV that lists the devices and channels that
    the UAV provides.
    """

    def __init__(self):
        """Constructor. Creates an empty device tree."""
        self._root = UAVNode()

    @property
    def json(self):
        """The JSON representation of the device tree."""
        return self._root.json

    @property
    def root(self):
        """The root node of the device tree."""
        return self._root
