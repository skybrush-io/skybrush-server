from __future__ import annotations

from typing import Sequence, Tuple, Union

__all__ = ("OSCAddress", "OSCAddressValuePair", "OSCValue")

#: Type alias for OSC addresses
OSCAddress = bytes

#: Type specification for values that can be sent in an OSC message
OSCValue = Union[bytes, int, float, Sequence["OSCValue"]]

#: Type alias for OSC address-value pairs, used in bundles
OSCAddressValuePair = Tuple[OSCAddress, Sequence[OSCValue]]
