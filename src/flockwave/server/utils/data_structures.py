from collections import defaultdict, OrderedDict

__all__ = ("keydefaultdict", "LastUpdatedOrderedDict")


class keydefaultdict(defaultdict):
    """defaultdict subclass that passes the key of the item being created
    to the default factory.
    """

    def __missing__(self, key):
        if self.default_factory is None:
            raise KeyError(key)
        else:
            ret = self[key] = self.default_factory(key)
            return ret


class LastUpdatedOrderedDict(OrderedDict):
    """OrderedDict subclass that stores items in the order the keys were
    _last_ added.
    """

    @property
    def first_value(self):
        """The first value in the ordered dict."""
        return next(iter(self.values()))

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
