from typing import OrderedDict, TypeVar

__all__ = ("LastUpdatedOrderedDict",)

K = TypeVar("K")
V = TypeVar("V")


class LastUpdatedOrderedDict(OrderedDict[K, V]):
    """OrderedDict subclass that stores items in the order the keys were
    _last_ added.
    """

    @property
    def first_value(self) -> V:
        """The first value in the ordered dict."""
        return next(iter(self.values()))

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
