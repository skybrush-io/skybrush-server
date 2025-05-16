"""Extension that adds support for Crazyflie drones."""

from .extension import construct, schema

__all__ = ("construct", "optional_dependencies", "schema", "tags")

description = "Support for Crazyflie drones"
dependencies = ("signals", "show", "lps")
optional_dependencies = {
    "rc": "allows one to control a Crazyflie drone with a remote controller"
}
tags = ("indoor",)
