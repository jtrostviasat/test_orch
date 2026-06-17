"""
Reusable input/return validation guards.

Two categories, used deliberately throughout the codebase:

  * `require_*` functions raise (ValueError/TypeError) — use them at trust
    boundaries (broker payloads, AMQP bodies, user/LDAP input, env-derived
    values). These must run even under `python -O`, so they never use `assert`.

  * Plain `assert` statements (in the call sites) guard internal invariants and
    "this should be impossible" conditions where compiling them out is fine.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def require(condition: bool, message: str) -> None:
    """
    Raise ``ValueError`` if a condition is falsy. Boundary-safe (not ``assert``).

    Args:
        condition: The boolean expression that must hold true.
        message: Human-readable error message used if the condition fails.

    Returns:
        None. Returns normally only when ``condition`` is truthy.

    Raises:
        ValueError: If ``condition`` is falsy.
    """
    if not condition:
        raise ValueError(message)


def require_non_empty_str(value: Any, name: str) -> str:
    """
    Validate that a value is a non-blank string.

    Args:
        value: The value to validate.
        name: Field name used in error messages.

    Returns:
        str: The original ``value`` unchanged (when valid).

    Raises:
        TypeError: If ``value`` is not a ``str``.
        ValueError: If ``value`` is empty or whitespace-only.
    """
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def require_positive_int(value: Any, name: str) -> int:
    """
    Validate that a value is an integer strictly greater than zero.

    Args:
        value: The value to validate (``bool`` is rejected even though it
            subclasses ``int``).
        name: Field name used in error messages.

    Returns:
        int: The original ``value`` unchanged (when valid).

    Raises:
        TypeError: If ``value`` is not an ``int`` (or is a ``bool``).
        ValueError: If ``value`` is <= 0.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


def require_non_negative_number(value: Any, name: str) -> float:
    """
    Validate that a value is a non-negative number and coerce it to ``float``.

    Args:
        value: The value to validate (``bool`` is rejected).
        name: Field name used in error messages.

    Returns:
        float: ``value`` converted to ``float`` (when valid).

    Raises:
        TypeError: If ``value`` is not an ``int``/``float`` (or is a ``bool``).
        ValueError: If ``value`` is negative.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return float(value)


def require_mapping(value: Any, name: str) -> Mapping:
    """
    Validate that a value is a mapping (dict-like).

    Args:
        value: The value to validate.
        name: Field name used in error messages.

    Returns:
        Mapping: The original ``value`` unchanged (when valid).

    Raises:
        TypeError: If ``value`` is not a ``Mapping``.
    """
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping, got {type(value).__name__}")
    return value


def require_keys(mapping: Mapping, keys: Iterable[str], name: str) -> None:
    """
    Validate that a mapping contains all required keys.

    Args:
        mapping: The mapping to inspect.
        keys: Iterable of key names that must all be present.
        name: Field name used in error messages.

    Returns:
        None. Returns normally only when every key is present.

    Raises:
        ValueError: If any required key is missing (message lists them sorted).
    """
    missing = [k for k in keys if k not in mapping]
    if missing:
        raise ValueError(f"{name} missing required keys: {', '.join(sorted(missing))}")
