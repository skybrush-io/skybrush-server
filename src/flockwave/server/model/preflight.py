from bidict import bidict  # type: ignore
from enum import Enum
from typing import Optional, Tuple

from flockwave.server.model.metamagic import ModelMeta
from flockwave.spec.schema import get_complex_object_schema

from .utils import enum_to_json


__all__ = ("PreflightCheckInfo",)


class PreflightCheckResult(Enum):
    """Possible outcomes for a single preflight check item."""

    OFF = "off"
    PASS = "pass"
    WARNING = "warning"
    RUNNING = "running"
    SOFT_FAILURE = "softFailure"
    FAILURE = "failure"
    ERROR = "error"


#: Ordering of preflight check results; used when summarizing the results of a
#: preflight checklist into a single result value. Larger values take precedence
#: over smaller ones
_numeric_preflight_check_results = bidict(
    {
        PreflightCheckResult.OFF: 0,
        PreflightCheckResult.PASS: 10,
        PreflightCheckResult.WARNING: 20,
        PreflightCheckResult.RUNNING: 30,
        PreflightCheckResult.SOFT_FAILURE: 40,
        PreflightCheckResult.FAILURE: 50,
        PreflightCheckResult.ERROR: 60,
    }
)


class PreflightCheckItem(metaclass=ModelMeta):
    """Class representing a single item in a detailed preflight check rpeort."""

    class __meta__:
        schema = get_complex_object_schema("preflightCheckItem")
        mappers = {"result": enum_to_json(PreflightCheckResult)}

    def __init__(
        self,
        id: str,
        label: Optional[str] = None,
        result: PreflightCheckResult = PreflightCheckResult.OFF,
    ):
        self.id = id
        self.label = label
        self.result = result


class PreflightCheckInfo(metaclass=ModelMeta):
    """Class representing the detailed result of the preflight checks on a
    single UAV.
    """

    class __meta__:
        schema = get_complex_object_schema("preflightCheckInfo")
        mappers = {"result": enum_to_json(PreflightCheckResult)}

    def __init__(self):
        self._in_progress = False
        self.result = PreflightCheckResult.OFF
        self.items = []
        self.update_summary()

    def add_item(
        self,
        id: str,
        label: Optional[str] = None,
        result: PreflightCheckResult = PreflightCheckResult.OFF,
    ) -> None:
        self.items.append(PreflightCheckItem(id=id, label=label, result=result))
        self.update_summary()

    def _get_result_from_items(self) -> Tuple[PreflightCheckResult, bool]:
        """Returns a single preflight check result summary based on the results
        of the individual items, and whether there is at least one check that
        is still running.
        """
        if not self.items:
            return PreflightCheckResult.OFF, False

        running = any(
            item.result is PreflightCheckResult.RUNNING for item in self.items
        )
        result = max(
            _numeric_preflight_check_results.get(item.result, 0) for item in self.items
        )
        return _numeric_preflight_check_results.inverse[result], running

    def clear(self) -> None:
        """Clears the preflight check items."""
        del self.items[:]
        self.update_summary()

    @property
    def failed(self) -> bool:
        """Returns whether at least one preflight check has failed, including
        "soft" failures that may resolve themselves on their own.
        """
        return self.result in (
            PreflightCheckResult.SOFT_FAILURE,
            PreflightCheckResult.FAILURE,
            PreflightCheckResult.ERROR,
        )

    @property
    def failed_conclusively(self) -> bool:
        """Returns whether the preflight checks failed conclusively, i.e. it is
        unlikely that the problems indicated by the preflight checks would
        resolve themselves without human intervention.
        """
        return self.result in (PreflightCheckResult.FAILURE, PreflightCheckResult.ERROR)

    @property
    def has_items(self) -> bool:
        """Returns whether the prelight check object has at least one item."""
        return bool(self.items)

    @property
    def in_progress(self) -> bool:
        """Returns whether the preflight checks are currently in progress (i.e.
        if there is at least one item where the status indicates that it is
        still in progress).
        """
        return self._in_progress

    @property
    def passed(self) -> bool:
        """Returns whether all the preflight checks have passed or are turned
        off. Preflight checks with warnings are considered to have passed.

        Also returns true if there are no preflight checks configured.
        """
        if self.in_progress:
            return False
        else:
            return self.result in (
                PreflightCheckResult.PASS,
                PreflightCheckResult.WARNING,
                PreflightCheckResult.OFF,
            )

    def get_result(self, id: str) -> PreflightCheckResult:
        """Returns a single preflight check result for the given individual id."""
        for item in self.items:
            if item.id == id:
                return item.result

        return PreflightCheckResult.OFF

    def set_result(
        self, id: str, result: PreflightCheckResult, label: Optional[str] = None
    ) -> None:
        """Updates the result of a single preflight check in this preflight
        check report.
        """
        changed = False

        for item in self.items:
            if item.id == id:
                if item.result != result:
                    item.result = result
                    changed = True
                item.label = label or None
                break

        if changed:
            self.update_summary()

    def update_summary(self) -> None:
        """Updates the summary of this preflight check report based on the
        results of the individual preflight check items.
        """
        self.result, self._in_progress = self._get_result_from_items()
