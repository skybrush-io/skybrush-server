"""Device and channel-related model classes."""

from __future__ import absolute_import

from flockwave.spec.schema import get_enum_from_schema, \
    get_complex_object_schema
from itertools import islice
from six import add_metaclass, iteritems
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

    def collect_channel_values(self):
        """Creates a Python dictionary that maps the IDs of the children of
        this node as follows:

          - channel nodes (i.e. instances of ChannelNode_) will be mapped
            to their current values

          - every other node ``node`` will be mapped to the result of
            ``node.collect_channel_values()``, recursively

        Returns:
            dict: a Python dictionary constructed as described above
        """
        return dict(
            (key, child.collect_channel_values())
            for key, child in iteritems(self.children)
        )

    def _add_child(self, id, node):
        """Adds the given node as a child node to this node.

        Parameters:
            id (str): the ID of the node
            node (DeviceTreeNodeBase): the node to add

        Returns:
            DeviceTreeNodeBase: the node that was added

        Throws:
            ValueError: if another node with the same ID already exists for
                this node
        """
        if not hasattr(self, "children"):
            self.children = {}
        if id in self.children:
            raise ValueError("another child node already exists with "
                             "ID={0!r}".format(id))
        self.children[id] = node
        return node

    def _remove_child(self, node):
        """Removes the given child node from this node.

        Parameters:
            node (DeviceTreeNodeBase): the node to remove

        Returns:
            DeviceTreeNodeBase: the node that was removed

        Throws:
            ValueError: if the node is not a child of this node
        """
        for id, child_node in iteritems(self.children):
            if child_node == node:
                return self._remove_child_by_id(id)
        raise ValueError("the given node is not a child of this node")

    def _remove_child_by_id(self, id):
        """Removes the child node with the given ID from this node.

        Parameters:
            id (str): the ID of the node to remove

        Returns:
            DeviceTreeNodeBase: the node that was removed

        Throws:
            ValueError: if there is no such child with the given ID
        """
        try:
            return self.children.pop(id)
        except KeyError:
            raise ValueError("no child exists with the given ID: {0!r}"
                             .format(id))


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
        self.value = None

    def collect_channel_values(self):
        """Returns the value of the channel itself."""
        return self.value

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


class RootNode(DeviceTreeNodeBase):
    """Class representing the root node in a Flockwave device tree."""

    def __init__(self):
        """Constructor."""
        super(RootNode, self).__init__()
        self.type = DeviceTreeNodeType.root

    def add_child(self, id, node):
        """Adds a new child node with the given ID to this root node.

        Parameters:
            id (str): the ID of the node to add
            node (UAVNode): the node to add; root nodes may only have UAV
                nodes as children.

        Returns:
            UAVNode: the node that was added

        Throws:
            ValueError: if another node with the same ID already exists for
                the root node
        """
        return self._add_child(id, node)

    def remove_child(self, node):
        """Removes the given child node from the root node.

        Parameters:
            node (UAVNode): the node to remove

        Returns:
            UAVNode: the node that was removed
        """
        return self._remove_child(node)

    def remove_child_by_id(self, id):
        """Removes the child node with the given ID from the root node.

        Parameters:
            id (str): the ID of the child node to remove

        Returns:
            UAVNode: the node that was removed
        """
        return self._remove_child_by_id(id)


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


class DeviceTreePath(object):
    """A path in a device tree from its root to one of its nodes. Leaf and
    branch nodes are both allowed.

    Device tree paths have a natural string representation that looks like
    standard filesystem paths: ``/node1/node2/node3/.../leaf``. This class
    allows you to construct a device tree path from a string. When a device
    tree path is printed as a string, it will also be formatted in this
    style.
    """

    def __init__(self, path=u"/"):
        """Constructor.

        Parameters:
            path (str): the string representation of the path.
        """
        self.path = path

    def iterparts(self):
        """Returns a generator that iterates over the parts of the path.

        Yields:
            str: the parts of the path
        """
        return islice(self._parts, 1, None)

    @property
    def path(self):
        """The path, formatted as a string.

        Returns:
            str: the path, formatted as a string
        """
        return u"/".join(self._parts)

    @path.setter
    def path(self, value):
        parts = value.split(u"/")
        if parts[0] != u"":
            raise ValueError("path must start with a slash")
        try:
            parts.index(u"", 1)
        except ValueError:
            # This is okay, this is what we wanted
            pass
        else:
            raise ValueError("path must not contain an empty component")
        self._parts = parts

    def __str__(self):
        return unicode(self).encode("utf-8")

    def __unicode__(self):
        return self.path


class DeviceTree(object):
    """A device tree of a UAV that lists the devices and channels that
    the UAV provides.
    """

    def __init__(self):
        """Constructor. Creates an empty device tree."""
        self._root = RootNode()

    @property
    def json(self):
        """The JSON representation of the device tree."""
        return self._root.json

    @property
    def root(self):
        """The root node of the device tree."""
        return self._root

    def resolve(self, path):
        """Resolves the given path in the tree and returns the node that
        corresponds to the given path.

        Parameters:
            path (Union[str, DeviceTreePath]): the path to resolve. Strings
                will be converted to a DeviceTreePath_ automatically.

        Returns:
            DeviceTreeNode: the node at the given path in the tree

        Throws:
            KeyError: if the given path cannot be resolved in the tree
        """
        if not isinstance(path, DeviceTreePath):
            path = DeviceTreePath(path)

        node = self.root
        for part in path.iterparts():
            node = node.children[part]
        return node
