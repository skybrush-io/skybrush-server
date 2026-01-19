"""Model-related classes for the Skybrush server."""

from .battery import BatteryInfo
from .builders import CommandExecutionStatusBuilder, FlockwaveMessageBuilder
from .channel import CommunicationChannel
from .client import Client
from .clock import Clock, ClockBase, StoppableClockBase
from .commands import CommandExecutionStatus, Progress
from .connection import ConnectionInfo, ConnectionPurpose, ConnectionStatus
from .devices import (
    ChannelNode,
    ChannelOperation,
    ChannelType,
    DeviceClass,
    DeviceNode,
    DeviceTree,
    DeviceTreeNodeType,
    DeviceTreeSubscriptionManager,
    ObjectNode,
)
from .error_set import ErrorSet
from .errors import ClientNotSubscribedError, NoSuchPathError
from .identifiers import default_id_generator
from .messages import FlockwaveMessage, FlockwaveNotification, FlockwaveResponse
from .object import ModelObject
from .uav import UAV, PassiveUAVDriver, UAVBase, UAVDriver, UAVStatusInfo
from .weather import Weather
from .world import World

__all__ = (
    "default_id_generator",
    "FlockwaveMessage",
    "FlockwaveMessageBuilder",
    "FlockwaveNotification",
    "FlockwaveResponse",
    "UAVStatusInfo",
    "UAVDriver",
    "UAV",
    "UAVBase",
    "ModelObject",
    "BatteryInfo",
    "Client",
    "ConnectionInfo",
    "ConnectionPurpose",
    "ConnectionStatus",
    "CommandExecutionStatus",
    "CommandExecutionStatusBuilder",
    "Clock",
    "ClockBase",
    "StoppableClockBase",
    "ChannelNode",
    "ChannelOperation",
    "ChannelType",
    "DeviceClass",
    "DeviceNode",
    "DeviceTree",
    "DeviceTreeNodeType",
    "ErrorSet",
    "ObjectNode",
    "DeviceTreeSubscriptionManager",
    "NoSuchPathError",
    "ClientNotSubscribedError",
    "World",
    "CommunicationChannel",
    "PassiveUAVDriver",
    "Weather",
    "Progress",
)
