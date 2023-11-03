"""Device and channel-related model classes."""

from __future__ import annotations

from blinker import Signal
from builtins import str
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from itertools import islice
from typing import (
    cast,
    overload,
    Any,
    Counter,
    Generic,
    Iterable,
    Optional,
    Type,
    TypeVar,
    TYPE_CHECKING,
    Union,
)

from flockwave.spec.schema import get_complex_object_schema

from .client import Client
from .errors import ClientNotSubscribedError, NoSuchPathError
from .metamagic import ModelMeta
from .object import ModelObject

if TYPE_CHECKING:
    from flockwave.server.registries.clients import ClientRegistry
    from flockwave.server.message_hub import MessageHub

__all__ = (
    "ChannelNode",
    "ChannelOperation",
    "ChannelType",
    "DeviceClass",
    "DeviceTree",
    "DeviceTreePath",
    "DeviceNode",
    "DeviceTreeNodeType",
    "ObjectNode",
    "DeviceTreeSubscriptionManager",
)


class ChannelOperation(Enum):
    READ = "read"
    WRITE = "write"


_channel_type_mapping: dict[Type, str] = {
    int: "number",
    float: "number",
    str: "string",
    bool: "boolean",
    object: "object",
}


class ChannelType(Enum):
    AUDIO = "audio"
    BOOLEAN = "boolean"
    BYTES = "bytes"
    CARTESIAN = "cartesian"
    COLOR = "color"
    DURATION = "duration"
    GPS = "gps"
    NUMBER = "number"
    OBJECT = "object"
    STRING = "string"
    TIME = "time"
    VIDEO = "video"

    @classmethod
    def from_object(cls, obj: Union["ChannelType", Type]):
        """Converts a Python type object to a corresponding channel type
        object. Also accepts ChannelType objects as input, in which case
        the object is returned as is.

        Parameters:
            obj: the type object to convert to a ChannelType

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
                raise TypeError(
                    f"{obj!r} cannot be converted to a ChannelType"
                ) from None
            return cls(name)


class DeviceClass(Enum):
    ACCELEROMETER = "accelerometer"
    ACTUATOR = "actuator"
    ALTIMETER = "altimeter"
    BATTERY = "battery"
    CAMERA = "camera"
    CPU = "cpu"
    CPU_CORE = "cpuCore"
    GPS = "gps"
    GROUP = "group"
    GYROSCOPE = "gyroscope"
    LED = "led"
    MAGNETOMETER = "magnetometer"
    MICROPHONE = "microphone"
    MISC = "misc"
    PYRO = "pyro"
    RADIO = "radio"
    RANGEFINDER = "rangefinder"
    RC = "rc"
    ROTOR = "rotor"
    SENSOR = "sensor"
    SPEAKER = "speaker"
    SPRAYER = "sprayer"


class DeviceTreeNodeType(Enum):
    ROOT = "root"
    OBJECT = "object"
    DEVICE = "device"
    CHANNEL = "channel"


C = TypeVar("C", bound="DeviceTreeNodeBase")


class DeviceTreeNodeBase(metaclass=ModelMeta):
    """Class representing a single node in a Flockwave device tree."""

    class __meta__:
        schema = get_complex_object_schema("deviceTreeNode")

    children: dict[str, "DeviceTreeNodeBase"]

    _subscribers: Optional[Counter[Client]]
    """Mapping that maps clients to the number of times they are subscribed to
    this node in the tree. Created lazily; ``None`` means that the mapping was
    not created yet.
    """

    _parent: Optional["DeviceTreeNodeBase"]
    """The parent of this node; ``None`` if this node is the root of the device
    tree.
    """

    _path: Optional[str]
    """The path string of this node, listing the names of all the nodes leading
    to this node from the root. Calculated lazily; ``None`` means that the
    path string was not calculated yet.
    """

    def __init__(self):
        """Constructor."""
        self._subscribers = None
        self._parent = None
        self._path = None

    def collect_channel_values(self) -> dict[str, Any]:
        """Creates a Python dictionary that maps the IDs of the children of
        this node as follows:

          - channel nodes (i.e. instances of ChannelNode_) will be mapped
            to their current values

          - every other node ``node`` will be mapped to the result of
            ``node.collect_channel_values()``, recursively

        Returns:
            a Python dictionary constructed as described above
        """
        return {
            key: child.collect_channel_values() for key, child in self.children.items()
        }

    def count_subscriptions_of(self, client: Client) -> int:
        """Count how many times the given client is subscribed to changes
        in channel values of this node or any of its sub-nodes.

        Parameters:
            client: the client to test

        Returns:
            the number of times time given client is subscribed to this node
        """
        return self._subscribers[client] if self._subscribers else 0

    @property
    def has_subscribers(self) -> bool:
        """Returns whether this node has at least one subscriber."""
        return bool(self._subscribers)

    def iterchildren(self) -> Iterable[tuple[str, "DeviceTreeNodeBase"]]:
        """Iterates over the children of this node.

        Yields:
            the ID of the child node and the child node itself, for all children
        """
        if hasattr(self, "children"):
            return self.children.items()
        else:
            return iter(())

    def iterparents(self, include_self: bool = False) -> Iterable["DeviceTreeNodeBase"]:
        """Iterates over the parents of this node, in increasing distance
        from the node itself.

        Parameters:
            include_self: whether to yield the node itself in the result

        Yields:
            the parents of this node, in increasing distance from the node itself
        """
        node = self if include_self else self._parent
        while node is not None:
            yield node
            node = node._parent

    def itersubscribers(self) -> Iterable[Client]:
        """Iterates over the subscribers registered at this node,
        reporting each subscriber only once even if it is registered
        multiple times.
        """
        if self._subscribers is not None:
            return self._subscribers.keys()
        else:
            return iter(())

    @property
    def parent(self) -> Optional["DeviceTreeNodeBase"]:
        """Returns the parent node of this node."""
        return self._parent

    @property
    def path(self) -> str:
        """Returns the path that leads to this node from the root node.

        This property is cached and invalidated automatically if the
        parent of this node or any of its parents change.

        Returns:
            a string representation of the path leading to this node from the
            root node. Resolving this path from the tree root should result in
            exactly this node.
        """
        if self._path is None:
            self._path = self._validate_path()
        return self._path

    def _validate_path(self) -> str:
        """Calculates the path string of this node."""
        node = self
        result = []
        while node is not None:
            parent = node._parent
            if parent is not None:
                for child_id, child in parent.iterchildren():
                    if child is node:
                        result.append(child_id)
                        break
                else:
                    raise ValueError(
                        "inconsistent tree: node not found "
                        "among the children of its parent"
                    )
            node = parent
        result.append("")
        result.reverse()
        return "/".join(result)

    @overload
    def traverse_dfs(
        self, own_id: None = None
    ) -> Iterable[tuple[Optional[str], "DeviceTreeNodeBase"]]:
        ...

    @overload
    def traverse_dfs(self, own_id: str) -> Iterable[tuple[str, "DeviceTreeNodeBase"]]:
        ...

    def traverse_dfs(
        self, own_id: Optional[str] = None
    ) -> Iterable[tuple[Optional[str], "DeviceTreeNodeBase"]]:
        """Returns a generator that yields all the nodes in the subtree of
        this node, including the node itself, in depth-first order.

        Parameters:
            own_id: the ID of this node in its parent, if known. This will be
                yielded in the traversal results for the node itself.

        Yields:
            each node in the subtree of this node, including the node itself,
            and its associated ID in its parent, in depth-first order. The ID
            will be the value of the ``own_id`` parameter for this node.
        """
        queue = [(own_id, self)]
        while queue:
            id, node = queue.pop()
            yield id, node
            queue.extend(node.iterchildren())

    @property
    def tree(self) -> Optional[DeviceTree]:
        """Returns the tree that this node is a part of."""
        node = self
        while node is not None and not isinstance(node, RootNode):
            node = node._parent
        return node if node is None else node.tree

    def _add_child(self, id: str, node: C) -> C:
        """Adds the given node as a child node to this node.

        Parameters:
            id: the ID of the node
            node: the node to add

        Returns:
            the node that was added

        Throws:
            ValueError: if another node with the same ID already exists for
                this node
        """
        if node._parent is not None:
            node._parent._remove_child(node)

        if not hasattr(self, "children"):
            self.children = {}
        if id in self.children:
            raise ValueError(
                "another child node already exists with " "ID={0!r}".format(id)
            )
        self.children[id] = node

        node._parent = self
        node._path = None

        return node

    def _dispose(self) -> None:
        """Marks the node as not being used any more and detaches it from
        its tree.
        """
        self._parent = None
        self._path = None

        if hasattr(self, "children"):
            for child in self.children.values():
                child._dispose()
            self.children = {}

    def _remove_child(self, node: C) -> C:
        """Removes the given child node from this node.

        Parameters:
            node: the node to remove

        Returns:
            the node that was removed

        Throws:
            ValueError: if the node is not a child of this node
        """
        for id, child_node in self.iterchildren():
            if child_node is node:
                return cast(C, self._remove_child_by_id(id))
        raise ValueError("the given node is not a child of this node")

    def _remove_child_by_id(self, id: str) -> "DeviceTreeNodeBase":
        """Removes the child node with the given ID from this node.

        Parameters:
            id: the ID of the node to remove

        Returns:
            the node that was removed

        Throws:
            ValueError: if there is no such child with the given ID
        """
        try:
            node = self.children.pop(id)
        except KeyError:
            raise ValueError(f"no child exists with the given ID: {id!r}") from None
        node._parent = None
        node._path = None
        return node

    def _subscribe(self, client: Client) -> None:
        """Subscribes the given client object to this node and its subtree.
        The client will get notified whenever one of the channels in the
        subtree of this node (or in the node itself if the node is a channel
        node) receives a new value.

        A client may be subscribed to this node multiple times; the node
        will track how many times the client has subscribed to the node and
        the client must unsubscribe exactly the same number of times to stop
        receiving notifications.

        Parameters:
            client (Client): the client to notify when a channel value
                changes in the subtree of this node.
        """
        if self._subscribers is None:
            # Create the subscriber counter lazily because most nodes
            # will not have any subscribers
            self._subscribers = Counter()
        self._subscribers[client] += 1

    def _unsubscribe(self, client: Client, force: bool = False) -> None:
        """Unsubscribes the given client object from this node and its
        subtree.

        Parameters:
            client (Client): the client to unsubscribe
            force (bool): whether to force an unsubscription of the client
                even if it is subscribed multiple times. Setting this
                argument to ``True`` will suppress ClientNotSubscribedError_
                exceptions if the client is not subscribed.

        Throws:
            KeyError: if the client is not subscribed to this node and
                ``force`` is ``False``
        """
        if self.count_subscriptions_of(client) > 0:
            assert self._subscribers is not None

            if force:
                del self._subscribers[client]
            else:
                new_count = self._subscribers[client] - 1
                if new_count == 0:
                    del self._subscribers[client]
                else:
                    self._subscribers[client] = new_count

        elif not force:
            raise KeyError(client)


T = TypeVar("T")


class ChannelNode(DeviceTreeNodeBase, Generic[T]):
    """Class representing a device node in a Flockwave device tree."""

    value: T
    """The value of the channel. Modifying this property will modify the value but
    _not_ notify any interested parties that the channel value was modified. Use
    the context manager returned by the ``create_mutator()`` method of the
    device tree instead if you want to notify interested parties about your
    modifications.
    """

    def __init__(
        self,
        channel_type: ChannelType,
        initial_value: T,
        *,
        operations: Optional[Iterable[ChannelOperation]] = None,
        unit: Optional[str] = None,
    ):
        """Constructor.

        Parameters:
            channel_type: the type of the channel
            operations: the allowed operations of the channel. Defaults to
                ``[ChannelOperation.READ]`` if set to ``None``.
            unit: the unit in which the value of the channel is expressed.
        """
        super().__init__()

        if operations is None:
            operations = [ChannelOperation.READ]

        self.type = DeviceTreeNodeType.CHANNEL
        self.subtype = channel_type
        self.operations = list(operations)
        self.unit = unit
        self.value = initial_value

    def collect_channel_values(self) -> T:
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

    def __init__(self, device_class: DeviceClass = DeviceClass.MISC):
        """Constructor."""
        super().__init__()
        self.type = DeviceTreeNodeType.DEVICE
        self.device_class = device_class

    def add_channel(
        self,
        id: str,
        type: Union[Type, ChannelType],
        *,
        initial_value: Any = None,
        unit: Optional[str] = None,
    ) -> ChannelNode:
        """Adds a new channel with the given identifier to this device
        node.

        Parameters:
            id: the identifier of the channel being added.
            type: the type of the channel
            unit: the unit in which the values of the channel are expressed

        Returns:
            the channel node that was added.
        """
        if (
            initial_value is None
            and isinstance(type, __builtins__["type"])
            and type != object
        ):
            initial_value = type()  # type: ignore

        channel_type = ChannelType.from_object(type)
        node = ChannelNode(channel_type, initial_value, unit=unit)
        return self._add_child(id, node)

    def add_device(
        self, id: str, device_class: DeviceClass = DeviceClass.MISC
    ) -> DeviceNode:
        """Adds a new device with the given identifier as a sub-device
        to this device node.

        Parameters:
            id: the identifier of the device being added.
            device_class: the class of the device being added

        Returns:
            the device tree node that was added.
        """
        return self._add_child(id, DeviceNode(device_class))

    @property
    def device_class(self):
        """Alias to ``deviceClass``."""
        return self.deviceClass

    @device_class.setter
    def device_class(self, value):
        self.deviceClass = value


class RootNode(DeviceTreeNodeBase):
    """Class representing the root node in a Flockwave device tree."""

    def __init__(self, tree: DeviceTree):
        """Constructor.

        Parameters:
            tree: the tree that this node will be placed in
        """
        super().__init__()
        self._tree = tree
        self.type = DeviceTreeNodeType.ROOT

    def add_child(self, id: str, node: ObjectNode) -> ObjectNode:
        """Adds a new child node with the given ID to this root node.

        Parameters:
            id: the ID of the node to add
            node: the node to add; root nodes may only have object nodes as
                children.

        Returns:
            the node that was added

        Throws:
            ValueError: if another node with the same ID already exists for
                the root node
        """
        return self._add_child(id, node)

    def remove_child(self, node: ObjectNode) -> ObjectNode:
        """Removes the given child node from the root node.

        Parameters:
            node: the node to remove

        Returns:
            the node that was removed
        """
        return self._remove_child(node)

    def remove_child_by_id(self, id: str) -> ObjectNode:
        """Removes the child node with the given ID from the root node.

        Parameters:
            id: the ID of the child node to remove

        Returns:
            the node that was removed
        """
        return cast(ObjectNode, self._remove_child_by_id(id))

    @DeviceTreeNodeBase.tree.getter
    def tree(self):
        return self._tree

    def _dispose(self):
        super(RootNode, self)._dispose()
        self._tree = None


class ObjectNode(DeviceTreeNodeBase):
    """Class representing an object node in a Flockwave device tree."""

    def __init__(self):
        """Constructor."""
        super().__init__()
        self.type = DeviceTreeNodeType.OBJECT

    def add_device(
        self, id: str, device_class: DeviceClass = DeviceClass.MISC
    ) -> DeviceNode:
        """Adds a new device with the given identifier to this node.

        Parameters:
            id: the identifier of the device being added.
            device_class: the class of the device being added.

        Returns:
            the device tree node that was added.
        """
        return self._add_child(id, DeviceNode(device_class))


@dataclass
class DeviceTreePath:
    """A path in a device tree from its root to one of its nodes. Leaf and
    branch nodes are both allowed.

    Device tree paths have a natural string representation that looks like
    standard filesystem paths: ``/node1/node2/node3/.../leaf``. This class
    allows you to construct a device tree path from a string. When a device
    tree path is printed as a string, it will also be formatted in this
    style.
    """

    def __init__(self, path: Union[str, "DeviceTreePath"] = "/"):
        """Constructor.

        Parameters:
            path: the string representation of the path, or another path object
                to clone.
        """
        if isinstance(path, DeviceTreePath):
            self._parts = list(path._parts)
        else:
            self.path = path

    def iterparts(self) -> Iterable[str]:
        """Returns a generator that iterates over the parts of the path.

        Yields:
            the parts of the path
        """
        return islice(self._parts, 1, None)

    @property
    def path(self) -> str:
        """The path, formatted as a string.

        Returns:
            the path, formatted as a string
        """
        return "/".join(self._parts)

    @path.setter
    def path(self, value: str) -> None:
        parts = value.split("/")
        if parts[0] != "":
            raise ValueError("path must start with a slash")
        if parts[-1] == "":
            parts.pop()
        try:
            parts.index("", 1)
        except ValueError:
            # This is okay, this is what we wanted
            pass
        else:
            raise ValueError("path must not contain an empty component")
        self._parts = parts

    def __str__(self) -> str:
        return self.path


class DeviceTree:
    """A device tree of an object that lists the devices and channels that
    the object provides.
    """

    channel_nodes_updated = Signal()
    """Signal that is dispatched by the tree when the _values_ of some of the
    channel nodes in the tree have been updated. Updates are detected only if
    they are made in the context of a DeviceTreeMutator_.
    """

    structure_changed = Signal()
    """Signal that is dispatched by the tree when the _structure_ of the tree
    changed.

    Right now we assume that the device trees of UAVs do not change, so we
    dispatch this signal only when a new UAV is added to the tree or when a
    UAV is removed.
    """

    def __init__(self):
        """Constructor. Creates an empty device tree."""
        self._root = RootNode(self)
        self._object_registry = None

    def create_mutator(self) -> "DeviceTreeMutator":
        """Creates a mutator object that provides additional methods to
        modify the values of the channels in the device tree and also notify
        subscribers about all modifications.

        The mutator object returned by this function should be used as a
        context manager as follows::

            with tree.create_mutator() as mutator:
                mutator.update(some_channel, new_value)
        """
        return DeviceTreeMutator(self, self._on_channel_nodes_updated)

    def dispose(self) -> None:
        """Disposes of this tree when it is not needed any more. No further
        operations should be performed on a tree after you have called
        ``dispose()`` on it.
        """
        self.root._dispose()

    @property
    def json(self):
        """The JSON representation of the device tree."""
        return self._root.json  # type: ignore

    @property
    def root(self):
        """The root node of the device tree."""
        return self._root

    def resolve(self, path: Union[str, DeviceTreePath]) -> DeviceTreeNodeBase:
        """Resolves the given path in the tree and returns the node that
        corresponds to the given path.

        Parameters:
            path: the path to resolve. Strings will be converted to a
                DeviceTreePath_ automatically.

        Returns:
            the node at the given path in the tree

        Throws:
            NoSuchPathError: if the given path cannot be resolved in the tree
        """
        if not isinstance(path, DeviceTreePath):
            path = DeviceTreePath(path)

        node = self.root
        for part in path.iterparts():
            try:
                node = node.children[part]
            except (KeyError, AttributeError):
                raise NoSuchPathError(path) from None

        return node

    def traverse_dfs(self) -> Iterable[tuple[Optional[str], DeviceTreeNodeBase]]:
        """Returns a generator that yields all the nodes in the tree in
        depth-first order.

        Yields:
            (str, DeviceTreeNode): each node in the tree and its associated
                ID in its parent, in depth-first order. The ID will be
                ``None`` for the root node.
        """
        return self.root.traverse_dfs()

    @property
    def object_registry(self):
        """The object registry that the device tree watches. The device tree
        will attach new object nodes when a new object is added to the registry,
        and similarly detach old object nodes when objects are removed from the
        registry.
        """
        return self._object_registry

    @object_registry.setter
    def object_registry(self, value):
        if self._object_registry == value:
            return

        if self._object_registry is not None:
            self._object_registry.added.disconnect(
                self._on_object_added, sender=self._object_registry
            )
            self._object_registry.removed.disconnect(
                self._on_object_removed, sender=self._object_registry
            )

        self._object_registry = value

        if self._object_registry is not None:
            self._object_registry.added.connect(
                self._on_object_added, sender=self._object_registry
            )
            self._object_registry.removed.connect(
                self._on_object_removed, sender=self._object_registry
            )

    def _on_channel_nodes_updated(self, nodes):
        """Callback method that a DeviceTreeMutator_ will call when a
        mutation session has ended and some channel nodes were updated.

        Parameters:
            nodes (Iterable[ChannelNode]): the set of channel nodes that
                were updated. It might not be a Python set but it is
                guaranteed to contain each affected node at most once.
        """
        # Just redispatch the set in a channel_nodes_updated signal
        self.channel_nodes_updated.send(self, nodes=nodes)

    def _on_object_added(self, sender, object: ModelObject):
        """Handler called when a new object is registered in the server.

        Parameters:
            sender: the object registry
            object: the object that was added
        """
        node = object.device_tree_node
        if node:
            self.root.add_child(object.id, node)
            self.structure_changed.send(self)

    def _on_object_removed(self, sender, object: ModelObject):
        """Handler called when an object is deregistered from the server.

        Parameters:
            sender: the object registry
            object: the object that was removed
        """
        if object.id in getattr(self.root, "children", ()):
            self.root.remove_child_by_id(object.id)
            self.structure_changed.send(self)


class DeviceTreeMutator:
    """Context manager that provides methods for modifying the values of the
    channel nodes in a device tree, records the modifications and then
    notifies the tree about the set of channel nodes that were modified.

    Attributes:
        tree (DeviceTree): the device tree that the mutator will modify
        callback (callable): an optional callable that the mutator
            will call with the set of updated nodes when the mutator goes
            out of context. The callback will not be called if there were
            no updated nodes.
    """

    def __init__(self, tree, callback):
        self.tree = tree
        self.callback = callback
        self._updated_nodes = set()

    def __enter__(self):
        self._updated_nodes = set()
        return self

    def __exit__(self, *args):
        if self._updated_nodes:
            self.callback(self._updated_nodes)

    def update(self, node, new_value):
        """Updates the value of a channel node at the given path with the
        given new value.

        The new value is compared with the old value using the standard
        Python equality operator. If the two values are equal, the value of
        the channel will _not_ be modified.

        Parameters:
            node (Union[str, DeviceTreePath, ChannelNode]): the path
                of the channel node to modify (either as a string or as a
                DeviceTreePath_), or the channel node itself.
            new_value (object): the new value of the channel
        """
        if not isinstance(node, DeviceTreeNodeBase):
            node = self.tree.resolve(node)

        assert isinstance(node, ChannelNode)

        if node.value == new_value:
            return

        node.value = new_value
        self._updated_nodes.add(node)


class DeviceTreeSubscriptionManager:
    """Object that is responsible for managing the subscriptions of clients
    to the nodes of a device tree and notifying clients when the values of
    the channel nodes change.
    """

    _tree: DeviceTree
    _client_registry: Optional["ClientRegistry"]
    _message_hub: "MessageHub"

    _pending_subscriptions: defaultdict[Client, list[DeviceTreePath]]
    """Dictionary mapping clients to device tree paths that they want to
    subscribe to but the paths do not exist yet.
    """

    def __init__(
        self,
        tree: DeviceTree,
        *,
        client_registry: Optional["ClientRegistry"],
        message_hub: "MessageHub",
    ):
        """Constructor.

        Parameters:
            tree: the tree whose subscriptions this object will manage
            client_registry: the client registry that enables the subscription
                manager to remove subscriptions of clients that have disconnected
        """
        self._tree = tree
        self._tree.channel_nodes_updated.connect(
            self._on_channel_nodes_updated, sender=self._tree
        )
        self._tree.structure_changed.connect(
            self._on_device_tree_structure_changed, sender=self._tree
        )
        self._client_registry = None
        self._message_hub = message_hub
        self._pending_subscriptions = defaultdict(list)

        self.client_registry = client_registry

    @property
    def client_registry(self) -> Optional["ClientRegistry"]:
        """The client registry that the device tree watches. The device tree
        will remove the subscriptions of clients from the tree when a
        client is removed from this registry.
        """
        return self._client_registry

    @client_registry.setter
    def client_registry(self, value: Optional["ClientRegistry"]) -> None:
        if self._client_registry == value:
            return

        if self._client_registry is not None:
            self._client_registry.removed.disconnect(
                self._on_client_removed, sender=self._client_registry
            )

        self._client_registry = value

        if self._client_registry is not None:
            self._client_registry.removed.connect(
                self._on_client_removed, sender=self._client_registry
            )

    @property
    def message_hub(self) -> "MessageHub":
        """The message hub that the subscription manager can use to inform
        subscribers about changes in the values of channel nodes
        """
        return self._message_hub

    def _collect_subscriptions(
        self,
        client: Client,
        path: DeviceTreePath,
        node: DeviceTreeNodeBase,
        result: Counter,
    ) -> None:
        """Finds all the subscriptions of the given client in the subtree
        of the given tree node (including the node itself) and adds tem to
        the given result object.

        Parameters:
            client: the client whose subscriptions we want to collect
            path: the path that leads to the root node. It will be mutated so
                make sure that you clone the original path of the node before
                passing it here.
            node (DeviceTreeNode): the root node that the search starts
                from
            result: the counter object that counts the subscriptions
        """
        count = node.count_subscriptions_of(client)
        if count > 0:
            result[str(path)] += count
        for child_id, child in node.iterchildren():
            path._parts.append(child_id)
            self._collect_subscriptions(client, path, child, result)
            path._parts.pop()

    def _find_device_tree_node_by_path(
        self, path: Union[str, DeviceTreePath], response=None
    ) -> Optional[DeviceTreeNodeBase]:
        """Finds a node in the global device tree based on a device tree
        path or registers a failure in the given response object if there
        is no such entry in the registry.

        Parameters:
            path: the path to find
            response (Optional[FlockwaveResponse]): the response in which
                the failure can be registered

        Returns:
            the device tree node at the given path or ``None`` if there is no
            such path
        """
        try:
            return self._tree.resolve(path)
        except NoSuchPathError:
            if hasattr(response, "add_error"):
                response.add_error(path, "No such device tree path")  # type: ignore
            return None

    def _notify_subscriber(self, subscriber: Client, channel_values: Any) -> None:
        """Notifies a single subscriber about the change in channel values
        in the subtree of a node that the subscriber is subscribed to.

        Parameters:
            subscriber: the client that has to be notified
            channel_values: object containing the channel values
                of all the channels in the subtree of the node, organized
                in exactly the same way as the tree itself is organized
                under the node
        """
        body = {"values": channel_values, "type": "DEV-INF"}
        message = self._message_hub.create_notification(body)

        self._message_hub.enqueue_message(message, to=subscriber)

    def _on_client_removed(self, sender: "ClientRegistry", client: Client) -> None:
        """Handler called when a client disconnected from the server."""
        for _, node in self._tree.traverse_dfs():
            node._unsubscribe(client, force=True)
        self._pending_subscriptions.pop(client, None)

    def _on_channel_nodes_updated(self, sender: DeviceTree, nodes):
        """Handler called when some channel nodes were updated in the
        associated device tree.
        """
        # For each node that was updated during this session, we have to
        # walk up the parent chain and collect all the parents.
        visited_nodes = set()
        for node in nodes:
            visited_nodes.update(node.iterparents(include_self=True))

        # Now, we need to construct the messages to be sent, for each
        # subscriber that was affected. Different subscribers may get
        # different messages so we need to build a message for each
        # subscriber.
        messages_by_subscribers = defaultdict(dict)
        for node in visited_nodes:
            if node.has_subscribers:
                path = node.path
                channel_values = node.collect_channel_values()
                for subscriber in node.itersubscribers():
                    messages_by_subscribers[subscriber][path] = channel_values

        # Now we can send the messages
        for subscriber, message in messages_by_subscribers.items():
            self._notify_subscriber(subscriber, message)

    def _on_device_tree_structure_changed(self, sender: DeviceTree):
        found: list[DeviceTreePath] = []

        for client, paths in self._pending_subscriptions.items():
            found.clear()
            for path in paths:
                try:
                    node = sender.resolve(path)
                except NoSuchPathError:
                    pass
                else:
                    found.append(path)
                    node._subscribe(client)

            if found:
                for path in found:
                    paths.remove(path)

    def create_DEV_INF_message_for(self, paths: Iterable[str], in_response_to=None):
        """Creates a DEV-INF message that contains information regarding
        the current values of the channels in the subtrees of the device
        tree matched by the given device tree paths.

        Parameters:
            paths: list of device tree paths
            in_response_to (Optional[FlockwaveMessage]): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the DEV-INF message with the current values of
                the channels in the subtrees matched by the given device
                tree paths
        """
        values = {}

        body = {"values": values, "type": "DEV-INF"}
        response = self._message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        for path in paths:
            node = self._find_device_tree_node_by_path(path, response)
            if node:
                values[path] = node.collect_channel_values()

        return response

    def list_subscriptions(self, client, path_filter):
        """Lists all the device tree paths that a client is subscribed
        to.

        Parameters:
            client (Client): the client whose subscriptions we want to
                retrieve
            path_filter (Optional[iterable]): iterable that yields strings
                or DeviceTreePath_ objects. The result will include only
                those subscriptions that are contained in at least one of
                the subtrees matched by the path filters.

        Returns:
            Counter: a counter object mapping device tree paths to the
                number of times the client has subscribed to them,
                multiplied by the number of times they were matched by
                the path filter.
        """
        if path_filter is None:
            path_filter = ("/",)

        result = Counter()
        for path in path_filter:
            node = self._tree.resolve(path)
            path_clone = DeviceTreePath(path)
            self._collect_subscriptions(client, path_clone, node, result)

        return result

    def subscribe(
        self, client: Client, path: Union[str, DeviceTreePath], lazy: bool = False
    ) -> None:
        """Subscribes the given client to the given device tree path.

        The same client may be subscribed to the same node multiple times;
        the same amount of unsubscription requests must follow to ensure
        that the client stops receiving notifications.

        Parameters:
            path: the path to resolve. Strings will be converted to a
                DeviceTreePath_ automatically.
            client: the client to subscribe
            lazy: whether the client is allowed to subscribe to paths that do
                not exist yet.

        Throws:
            NoSuchPathError: if the given path cannot be resolved in the tree
        """
        try:
            self._tree.resolve(path)._subscribe(client)
        except NoSuchPathError:
            if lazy:
                self._pending_subscriptions[client].append(DeviceTreePath(path))
            else:
                raise

    def unsubscribe(
        self, client: Client, path: Union[str, DeviceTreePath], force: bool = False
    ) -> None:
        """Unsubscribes the given client from the given device tree path.

        The same client may be subscribed to the same node multiple times;
        the same amount of unsubscription requests must follow to ensure
        that the client stops receiving notifications. Alternatively, you
        may set the ``force`` argument to ``True`` to force the removal
        of the client from the node no matter how many times it has
        subscribed before.

        Parameters:
            path: the path to resolve. Strings will be converted to a
                DeviceTreePath_ automatically.
            client: the client to unsubscribe
            force: whether to force an unsubscription of the client
                even if it is subscribed multiple times. Setting this
                argument to ``True`` will suppress ClientNotSubscribedError_
                exceptions if the client is not subscribed.

        Throws:
            NoSuchPathError: if the given path cannot be resolved in the tree
            ClientNotSubscribedError: if the given client is not subscribed
                to the node and ``force`` is ``False``
        """
        try:
            self._tree.resolve(path)._unsubscribe(client, force)
        except NoSuchPathError:
            try:
                self._pending_subscriptions[client].remove(DeviceTreePath(path))
            except ValueError:
                raise ClientNotSubscribedError(client, path) from None
        except KeyError:
            raise ClientNotSubscribedError(client, path) from None
