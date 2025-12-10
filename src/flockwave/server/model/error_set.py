from collections.abc import Set
from typing import Iterable, Iterator, Sequence

__all__ = ("ErrorSet",)


class ErrorSet(Set[int]):
    """Data structure to store a list of non-negative numeric error codes
    efficiently.
    """

    _errors: set[int]

    def __init__(self, errors: Iterable[int] | None = None) -> None:
        self._errors = set(errors) if errors is not None else set()

    def __contains__(self, item) -> bool:
        return item in self._errors

    def __iter__(self) -> Iterator[int]:
        return iter(self._errors)

    def __len__(self) -> int:
        return len(self._errors)

    @property
    def json(self) -> Sequence[int]:
        return sorted(self._errors)

    def clear(self) -> None:
        """Clears all error codes."""
        self._errors.clear()

    def ensure(self, code: int, present: bool = True) -> None:
        """Ensures that the given error code is present (or not present) in the
        error code list.

        Parameters:
            code: the code to add or remove
            present: whether to add the code (True) or remove it (False)
        """
        # If the error code is to be cleared and we don't have any errors
        # (which is the common code path), we can bail out immediately.
        if not self._errors and not present:
            return

        code = int(code)
        if code in self._errors:
            if not present:
                self._errors.remove(code)
        else:
            if present:
                self._errors.add(code)

    def ensure_many(self, codes: dict[int, bool]) -> None:
        """Updates multiple error codes with a single function call.

        Parameters:
            codes: dictionary mapping error codes to a boolean specifying
                whether the error code should be present or absent
        """
        # If all error codes are to be cleared and we don't have any errors
        # (which is the common code path), we can bail out immediately.
        if not self._errors and not any(present for present in codes.values()):
            return

        for code, present in codes.items():
            code = int(code)
            if code in self._errors:
                if not present:
                    self._errors.remove(code)
            else:
                if present:
                    self._errors.add(code)

    def set(self, codes: Iterable[int]) -> None:
        """Replaces the current error code list with the given list.

        Parameters:
            codes: the new list of error codes
        """
        self._errors.clear()
        self._errors.update(codes)
