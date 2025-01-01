from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from functools import partial, wraps
from math import inf, isfinite
from typing import Any, Optional

from flockwave.ext.errors import ApplicationExit, NotLoadableError, NotSupportedError
from flockwave.networking import get_link_layer_address_mapping

__all__ = ("load", "only_with_feature", "unload")


NEVER_EXPIRES = 20 * 365
"""Symbolic constant to return from get_days_left_until_expiry() if the license
never expires.
"""

license: Optional[License] = None
"""Global variable holding the current license."""

_EMPTY_SET = frozenset()
"""Empty set used by the default implementation of License.get_features"""


class License(ABC):
    """Abstraction layer to help us with switching to different license managers
    if we want to.
    """

    @abstractmethod
    def get_allowed_hardware_ids(self) -> Optional[tuple[str, ...]]:
        """Returns a tuple containing the hardware IDs associated to the
        license, or `None` if the license does not have hardware ID
        restrictions.
        """
        raise NotImplementedError

    @abstractmethod
    def get_allowed_mac_addresses(self) -> Optional[tuple[str, ...]]:
        """Returns a tuple containing the MAC addresses associated to the
        license, or `None` if the license does not have MAC address
        restrictions.
        """
        raise NotImplementedError

    def get_days_left_until_expiry(self) -> int:
        """Returns the number of days left until the expiry of the license;
        returns at least 20 years if the license never expires. Returns zero
        if the license expires today.
        """
        expiry_date = self.get_expiry_date()
        if expiry_date is None:
            return NEVER_EXPIRES
        else:
            return (expiry_date - date.today()).days

    @abstractmethod
    def get_features(self) -> frozenset[str]:
        """Returns the list of additional features that the license provides
        access for. Features are simple string identifiers; it is up to the
        host application to interpret them as appropriate.
        """
        return _EMPTY_SET

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

    @abstractmethod
    def get_expiry_date(self) -> Optional[date]:
        """Returns the date on which the license expires (but is still valid),
        or `None` if the license never expires.
        """
        raise NotImplementedError

    def has_feature(self, feature: str) -> bool:
        """Returns whether the license provides access to the given feature."""
        return feature in self.get_features()

    def is_valid(self) -> bool:
        """Returns whether the license is valid."""
        # Check date restriction
        if self.get_days_left_until_expiry() < 0:
            return False

        # Check MAC address restriction
        allowed_mac_addresses = self.get_allowed_mac_addresses()
        if allowed_mac_addresses:
            all_mac_addresses = set(get_link_layer_address_mapping().values())
            if not any(addr in all_mac_addresses for addr in allowed_mac_addresses):
                return False

        # Check hardware ID restriction
        allowed_hardware_ids = self.get_allowed_hardware_ids()
        if allowed_hardware_ids:
            try:
                from cls import get_hardware_id

                own_hardware_id = get_hardware_id()
            except ImportError:
                return False

            if own_hardware_id not in allowed_hardware_ids:
                return False

        return True

    @property
    def json(self):
        """Returns the JSON representation of this license in the format used
        by the LCN-INF message.
        """
        result: dict[str, Any] = {"id": self.get_id(), "licensee": self.get_licensee()}

        expiry_date = self.get_expiry_date()
        if expiry_date is not None:
            result["expiryDate"] = expiry_date.strftime("%Y-%m-%d")

        # ######################################################################

        restrictions = []

        allowed_hardware_ids = self.get_allowed_hardware_ids()
        if allowed_hardware_ids:
            allowed_short_ids = [id[:8] for id in allowed_hardware_ids]
            if len(allowed_hardware_ids) > 2:
                num_extra = len(allowed_hardware_ids) - 2
                formatted_hardware_ids = (
                    ", ".join(allowed_short_ids[:2]) + f" and {num_extra} more"
                )
            else:
                formatted_hardware_ids = " and ".join(allowed_short_ids)

            restrictions.append(
                {
                    "type": "mac",
                    "label": "Restricted to hardware ID",
                    "secondaryLabel": formatted_hardware_ids,
                    "parameters": {"ids": allowed_hardware_ids},
                }
            )

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

        num_drones = self.get_maximum_drone_count()
        if isfinite(num_drones) and num_drones >= 0:
            restrictions.append(
                {
                    "type": "drones",
                    "label": "Maximum number of drones",
                    "secondaryLabel": str(int(num_drones)),
                    "parameters": {"maxCount": int(num_drones)},
                }
            )

        if restrictions:
            result["restrictions"] = restrictions

        # ######################################################################

        features = []
        if self.has_feature("pro"):
            features.append(
                {
                    "type": "pro",
                    "label": "Skybrush Server Pro features",
                }
            )

        if features:
            result["features"] = features

        return result


class DummyLicense(License):
    """License class used for testing purposes."""

    def get_allowed_hardware_ids(self):
        return None

    def get_allowed_mac_addresses(self):
        return None

    def get_expiry_date(self) -> Optional[date]:
        return date.today() + timedelta(days=42)

    def get_id(self) -> str:
        return "test-1"

    def get_licensee(self) -> str:
        return "Test license"

    def get_maximum_drone_count(self) -> float:
        return 5


class DictBasedLicense(License):
    """License class for licenses that provide a standardized dictionary
    that describes the capabilities of the license.

    The dictionary should have the following keys:

    - ``id`` - the unique identifier of the license. Must be a string.

    - ``licensee`` - name of the licensee. Must be a string.

    - ``cond`` - conditions that restrict the usage of the license. This must
      map to another dictionary; condition subkeys will be described below.
      The key may be omitted, in which case it is assumed that it maps to an
      empty dictionary (i.e. no restrictions).

    - ``expiresAt`` - POSIX timestamp in seconds when the license will expire.
      The license is perpetual if this key is missing.

    - ``features`` - additional features provided by the license. This is an
      unordered set of strings; interpreting the strings is up to the host
      application.

    The condition dictionary can have the following keys:

    - ``hwid`` - list of hardware IDs that hte license is valid for

    - ``mac`` - list of MAC addresses that the license is valid for

    - ``drones`` - maximum number of drones that can be used simultaneously"""

    _license_info: Mapping[str, Any]
    _features: frozenset[str]

    def __init__(self, license_info: Mapping[str, Any]):
        """Constructor.

        Do not use directly; use one of the subclasses instead.
        """
        self._license_info = license_info
        self._update_features()

    def get_allowed_hardware_ids(self) -> tuple[str, ...] | None:
        hwids = self._get_conditions().get("hwid")
        return tuple(str(x) for x in hwids) if hwids is not None else None

    def get_allowed_mac_addresses(self) -> Optional[tuple[str, ...]]:
        addresses = self._get_conditions().get("mac")

        # An earlier bug in cmtool sometimes added empty MAC addresses to the
        # license; we fix it here
        if addresses is not None:
            return tuple(address for address in addresses if address)
        else:
            return None

    def get_expiry_date(self) -> Optional[date]:
        expiry = self._license_info.get("expiry")
        if expiry is None:
            return None

        try:
            return datetime.strptime(expiry, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"invalid expiry date: {expiry!r}") from None

    def get_features(self) -> frozenset[str]:
        return self._features

    def get_id(self) -> str:
        try:
            return str(self._license_info["id"])
        except KeyError:
            return ""

    def get_licensee(self) -> str:
        try:
            return str(self._license_info["licensee"])
        except KeyError:
            return ""

    def get_maximum_drone_count(self) -> float:
        cond = self._get_conditions()
        try:
            drone_count = cond["drones"]
        except KeyError:
            drone_count = inf
        if not isinstance(drone_count, (int, float)):
            return 0
        else:
            return float(int(drone_count)) if drone_count < inf else inf

    def _get_conditions(self) -> Mapping[str, Any]:
        try:
            return self._license_info["cond"]
        except KeyError:
            return {}

    def _update_features(self) -> None:
        features = self._license_info.get("features")
        self._features = frozenset(str(f) for f in features) if features else _EMPTY_SET


class CLSLicense(DictBasedLicense):
    """License class for licenses based on CollMot's own license manager."""

    @classmethod
    def get_license(cls):
        from cls import license

        if isinstance(license, Mapping) and len(license) > 0:
            return cls(license)
        else:
            raise RuntimeError("no license")


def get_license() -> Optional[License]:
    """Returns the currently loaded license, or `None` if there is no license
    associated to the app.
    """
    global license
    return license


def has_feature(*args: str) -> bool:
    """Returns whether the currently loaded license is valid and possesses _any_
    of the given features in the arguments.

    Returns ``False`` if no license is loaded, or if a license is loaded but the
    function has no arguments.
    """
    license = get_license()
    return license is not None and any(license.has_feature(feature) for feature in args)


def enforce_license_limits(license: Optional[License], app) -> None:
    num_drones = license.get_maximum_drone_count() if license else inf
    app.object_registry.size_limit = num_drones


def show_license_information(
    license: Optional[License], logger, *, with_restrictions: bool = False
) -> None:
    """Shows detailed information about the current license in the application
    logs at startup.

    Parameters:
        license: the currenet license
        logger: the logger to print the information to
        with_restrictions: whether to also log additional restrictions related
            to the license
    """
    if license is None:
        return

    licensee = license.get_licensee()
    if licensee:
        logger.info(f"Licensed to {licensee}")

    try:
        from cls import get_hardware_id
    except ImportError:
        # No license manager or it is older than 3.0.0
        pass
    else:
        logger.info(f"Hardware ID: {get_hardware_id()}")

    if with_restrictions:
        for restriction in license.json.get("restrictions", ()):
            if isinstance(restriction, dict):
                labels = [
                    str(restriction.get(key, "")) for key in ("label", "secondaryLabel")
                ]
                logger.info(": ".join(label for label in labels if label))

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

    license_factories = [
        CLSLicense.get_license,
    ]

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
    show_license_information(license, logger, with_restrictions=True)

    app.message_hub.register_message_handler(handle_LCN_INF, "LCN-INF")


def only_with_feature(*features: str, sync: bool = False):
    """Decorator factory that creates a decorator that restricts the execution
    of a function to the case when the currently loaded license is valid and
    has a given feature (or one of the provided features).

    For technical reasons, you need to specify explicitly whether you are
    providing the decorator with a sync or an async function.
    """
    feature_str = ", ".join(features)
    if len(features) > 1:
        message = f"This extension requires a license with the following entitlements: {feature_str}"
    else:
        message = (
            f"This extension requires a license with the {feature_str!r} entitlement"
        )
    permitted = partial(has_feature, *features)

    if sync:

        def decorator(func):
            @wraps(func)
            def decorated(*args, **kwds):
                if not permitted():
                    raise NotLoadableError(message)
                return func(*args, **kwds)

            return decorated

        return decorator

    else:

        def async_decorator(func):
            @wraps(func)
            async def decorated(*args, **kwds):
                if not permitted():
                    raise NotLoadableError(message)
                return await func(*args, **kwds)

            return decorated

        return async_decorator


def unload(app):
    if not app.extension_manager.shutting_down:
        raise NotSupportedError("License manager cannot be unloaded")

    global license

    app.message_hub.unregister_message_handler(handle_LCN_INF, "LCN-INF")

    enforce_license_limits(None, app)
    license = None


description = "License management"
exports = {"get_license": get_license, "has_feature": has_feature}
schema = {}
