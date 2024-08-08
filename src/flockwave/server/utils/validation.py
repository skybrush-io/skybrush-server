from cachetools import cached, LRUCache
from typing import Any

from flockwave.spec.validator import (
    Validator,
    create_validator_for_schema,
    ValidationError,
)

__all__ = ("cached_validator_for", "validator_for", "Validator", "ValidationError")


def validator_for(schema: Any) -> Validator:
    """Creates a validator for the given JSON schema.

    Returns:
        the validator function of the schema
    """
    return create_validator_for_schema(schema)


@cached(cache=LRUCache(maxsize=128), key=id)
def cached_validator_for(schema: Any) -> Validator:
    """Cached version of `validator_for()`. Useful when you need the validator for
    the same schema multiple times and you cannot store the validator locally.
    """
    return validator_for(schema)
