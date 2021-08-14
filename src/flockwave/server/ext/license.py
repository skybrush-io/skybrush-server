from abc import abstractmethod, ABCMeta
from datetime import date, timedelta
from flockwave.ext.errors import ApplicationExit
from flockwave.networking import get_link_layer_address_mapping
from math import inf
from typing import cast, Any, Dict, Optional, Tuple

import json

__all__ = ("load",)


#: Symbolic constant to return from get_days_left_until_expiry() if the license
#: never expires
NEVER_EXPIRES = 20 * 365


#: Global variable holding the current license
license = None  # type: Optional[License]


class License(metaclass=ABCMeta):
    """Abstraction layer to help us with switching to different license managers
    if we want to.
    """

    @abstractmethod
    def get_allowed_mac_addresses(self) -> Optional[Tuple[str]]:
        """Returns a tuple containing the MAC addresses associated to the
        license, or `None` if the license does not have MAC address
        restrictions.
        """
        raise NotImplementedError

    @abstractmethod
    def get_days_left_until_expiry(self) -> int:
        """Returns the number of days left until the expiry of the license;
        returns at least 20 years if the license never expires. Returns zero
        if the license expires today.
        """
        raise NotImplementedError

    @abstractmethod
    def get_id(self) -> str:
        """Returns a unique ID of the license."""
        raise NotImplementedError

    @abstractmethod
    def get_licensee(self) -> str:
        """Returns the licensee of this license."""
        raise NotImplementedError

    @abstractmethod
    def get_maximum_drone_count(self) -> float:
        """Returns the maximum number of drones that the user is allowed to
        manage with this license; returns infinity if the license does not
        impose a restriction on the number of drones.
        """
        raise NotImplementedError

    def get_expiry_date(self) -> Optional[date]:
        """Returns the date on which the license expires (but is still valid),
        or `None` if the license never expires.
        """
        days_left = self.get_days_left_until_expiry()
        if days_left >= NEVER_EXPIRES:
            return None
        else:
            return date.today() + timedelta(days=days_left)

    def is_valid(self) -> bool:
        """Returns whether the license is valid."""
        # Check date restriction
        if self.get_days_left_until_expiry() < 0:
            return False

        # Check MAC address restriction
        allowed_mac_addresses = self.get_allowed_mac_addresses()
        if allowed_mac_addresses:
            all_mac_addresses = {
                addr for addr in get_link_layer_address_mapping().values()
            }
            if not any(addr in all_mac_addresses for addr in allowed_mac_addresses):
                return False

        return True

    @property
    def json(self):
        """Returns the JSON representation of this license in the format used
        by the LCN-INF message.
        """
        result: Dict[str, Any] = {"id": self.get_id(), "licensee": self.get_licensee()}

        expiry_date = self.get_expiry_date()
        if expiry_date is not None:
            result["expiryDate"] = expiry_date.strftime("%Y-%m-%d")

        restrictions = []

        allowed_mac_addresses = self.get_allowed_mac_addresses()
        if allowed_mac_addresses:
            if len(allowed_mac_addresses) > 2:
                num_extra = len(allowed_mac_addresses) - 2
                formatted_mac_addresses = (
                    ", ".join(allowed_mac_addresses[:2]) + f" and {num_extra} more"
                )
            else:
                formatted_mac_addresses = " and ".join(allowed_mac_addresses)

            restrictions.append(
                {
                    "type": "mac",
                    "label": "Restricted to MAC address",
                    "secondaryLabel": formatted_mac_addresses,
                    "parameters": {"addresses": allowed_mac_addresses},
                }
            )

        if restrictions:
            result["restrictions"] = restrictions

        return result


class DummyLicense(License):
    """License class used for testing purposes."""

    def get_allowed_mac_addresses(self):
        return None

    def get_days_left_until_expiry(self) -> int:
        return 42

    def get_id(self) -> str:
        return "test-1"

    def get_licensee(self) -> str:
        return "Test license"

    def get_maximum_drone_count(self) -> float:
        return 5


class PyArmorLicense(License):
    """License class for PyArmor-based licenses."""

    @classmethod
    def get_license(cls):
        try:
            from pytransform import get_license_info, get_expired_days

            return cls(get_license_info(), get_expired_days())
        except ImportError:
            return None

    def __init__(self, license_info: Dict[str, Any], expired_days: int = -1):
        """Constructor.

        Do not use directly; use the `get_license()` class method instead.
        """
        # PyArmor returns 1 if the license expires today and never returns zero
        # so we need to subtract 1
        self._expired_days = (expired_days - 1) if expired_days > 0 else NEVER_EXPIRES
        self._license_info = license_info

    def get_allowed_mac_addresses(self) -> Optional[Tuple[str]]:
        return self._get_conditions().get("mac")

    def get_days_left_until_expiry(self) -> int:
        return self._expired_days

    def get_id(self) -> str:
        return self._license_info.get("CODE") or ""

    def get_licensee(self) -> str:
        parsed = self._parse_license_info()
        return str(parsed.get("licensee", ""))

    def get_maximum_drone_count(self) -> float:
        return self._get_conditions().get("drones", inf)

    def _get_conditions(self) -> Dict[str, Any]:
        parsed = self._parse_license_info()
        return parsed.get("cond", {})

    def _parse_license_info(self) -> Dict[str, Any]:
        if not hasattr(self, "_parsed_license_info"):
            data = self._license_info.get("DATA")
            if data:
                try:
                    data = json.loads(data)
                    if not isinstance(data, dict):
                        data = None
                except Exception:
                    data = None

            self._parsed_license_info = data or {}

        return cast(Dict[str, Any], self._parsed_license_info)


def get_license() -> Optional[License]:
    """Returns the currently loaded license, or `None` if there is no license
    associated to the app.
    """
    global license
    return license


def enforce_license_limits(license: Optional[License], app) -> None:
    num_drones = license.get_maximum_drone_count() if license else inf
    app.object_registry.size_limit = num_drones


def show_license_information(license: Optional[License], logger) -> None:
    """Shows detailed information about the current license in the application
    logs at startup.
    """
    if license is None:
        return

    licensee = license.get_licensee()
    if licensee:
        logger.info(f"Licensed to {licensee}")

    days_left = license.get_days_left_until_expiry()
    if days_left >= NEVER_EXPIRES:
        pass
    elif days_left >= 15:
        logger.info(f"Your license key expires in {days_left} days")
    elif days_left > 1:
        logger.warn(
            f"Your license key expires in {days_left} days. Contact us for renewal."
        )
    elif days_left == 1:
        logger.warn("Your license key expires in one day. Contact us for renewal.")
    elif days_left == 0:
        logger.warn("Your license key expires today. Contact us for renewal.")


#############################################################################


def handle_LCN_INF(message, sender, hub):
    global license
    return {"license": license.json} if license else {"license": {"id": ""}}


#############################################################################


def load(app, configuration, logger):
    global license

    # License factories must raise an ApplicationExit exception if they have
    # found a license and it is not valid
    license_factories = [PyArmorLicense.get_license]

    for factory in license_factories:
        try:
            license = factory()
        except Exception:
            # Move on and try the next factory
            pass
        else:
            # The first license that works is used
            break

    if license and not license.is_valid():
        raise ApplicationExit("License expired or is not valid for this machine")

    enforce_license_limits(license, app)
    show_license_information(license, logger)

    app.message_hub.register_message_handler(handle_LCN_INF, "LCN-INF")


def unload(app):
    global license

    app.message_hub.unregister_message_handler(handle_LCN_INF, "LCN-INF")

    enforce_license_limits(None, app)
    license = None


description = "License management"
exports = {"get_license": get_license}
schema = {}
