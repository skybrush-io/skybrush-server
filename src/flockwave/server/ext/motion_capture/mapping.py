import re

from dataclasses import dataclass, field
from enum import Enum
from typing import cast, Any, Callable, Dict, Iterable, List, Optional

from flockwave.server.utils.generic import constant, identity


class NameRemappingRuleType(Enum):
    """Enum describing the possible rules that can be applied to a name during
    a remapping to UAV IDs.
    """

    ACCEPT = "accept"
    """Rule that accepts all incoming names unconditionally."""

    REJECT = "reject"
    """Rule that rejects all incoming names unconditionally."""

    STRIP_PREFIX = "strip_prefix"
    """Rule that strips a prefix from the name if it is present."""

    STRIP_SUFFIX = "strip_suffix"
    """Rule that strips a suffix from the name if it is present."""

    REGEX = "regex"
    """Rule that matches a regular expression to the name and replaces it with
    the match itself.
    """


@dataclass
class NameRemappingRule:
    """A single name remapping rule in the remapping class."""

    type: NameRemappingRuleType
    """The type of the remapping rule"""

    value: str = ""
    """The value of the remapping rule (the prefix or suffix to strip, or the
    regular expression to match).
    """

    _func: Callable[[str], Optional[str]] = field(init=False)

    @classmethod
    def from_configuration(cls, config: Dict[str, Any]):
        """Creates a name remapping rule object from the format used in the
        configuration of this extension.
        """
        return cls(
            type=NameRemappingRuleType(str(config.get("type", "accept"))),
            value=str(config.get("value", "")),
        )

    def __post_init__(self):
        if self.type is NameRemappingRuleType.ACCEPT:
            self._func = identity
        elif self.type is NameRemappingRuleType.REJECT:
            self._func = constant(None)
        elif self.type is NameRemappingRuleType.STRIP_PREFIX:
            self._func = strip_prefix(self.value)
        elif self.type is NameRemappingRuleType.STRIP_SUFFIX:
            self._func = strip_suffix(self.value)
        elif self.type is NameRemappingRuleType.REGEX:
            self._func = matches_regex(self.value)
        else:
            self._func = constant(None)

    def apply(self, name: str) -> Optional[str]:
        """Applies this rule to the given name.

        Returns:
            the remapped name or ``None`` if the rule rejected the name and
            wants to stop processing
        """
        if type is NameRemappingRuleType.ACCEPT:
            return name
        elif type is NameRemappingRuleType.REJECT:
            return None
        else:
            return self._func(name)


class NameRemapping:
    """Helper class that remaps the names of the rigid bodies in motion capture
    frames based on a list of rules configured by the user.
    """

    _rules: List[NameRemappingRule]
    """The list of rules in the remapping class."""

    @classmethod
    def from_configuration(cls, config: Dict[str, Any]):
        """Creates a name remapping object from the format used in the configuration
        of this extension.
        """
        rules = config.get("rules")
        if not rules or not hasattr(rules, "__iter__"):
            rules = ()

        result = cls()
        for rule_spec in cast(Iterable[Dict[str, Any]], rules):
            result.add_rule(NameRemappingRule.from_configuration(rule_spec))

        return result

    def __init__(self):
        self._rules = []

    def __call__(self, name: str) -> Optional[str]:
        """Applies the rules to the given name.

        Returns:
            the remapped naem if the name was accepted, or ``None`` if the name
            was rejected
        """
        maybe_name: Optional[str] = name
        for rule in self._rules:
            maybe_name = rule.apply(name)
            if maybe_name is None:
                return None
            name = maybe_name
        return name

    def add_rule(self, rule: NameRemappingRule) -> None:
        """Adds a new rule to the list of rules."""
        self._rules.append(rule)


def strip_prefix(prefix: str) -> Callable[[str], str]:
    length = len(prefix)

    def func(value: str) -> str:
        return value[length:] if value.startswith(prefix) else value

    return func


def strip_suffix(suffix: str) -> Callable[[str], str]:
    length = len(suffix)

    def func(value: str) -> str:
        return value[:-length] if value.endswith(suffix) else value

    return func


def matches_regex(regex: str) -> Callable[[str], str]:
    compiled = re.compile(regex)

    def func(value: str) -> str:
        match = compiled.match(value)
        return match.group(0) if match else value

    return func
