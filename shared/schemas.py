"""
Cross-service data contracts.

`TestBundleRequest` is validated on construction so a malformed bundle cannot be
serialized onto the broker in the first place.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from shared.validation import (
    require_keys,
    require_mapping,
    require_non_empty_str,
    require_positive_int,
)


class TestStatus(str, Enum):
    """Lifecycle states for a single test execution record."""

    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class TestBundleRequest:
    """Validated test-bundle job contract that crosses the RabbitMQ broker."""

    test_id: str
    user_id: int
    runner_type: str
    framework_image: str
    required_hw_tags: List[str] = field(default_factory=list)
    requires_emulation: bool = False
    quali_reservation_id: Optional[str] = None
    runner_variables: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """
        Validate all fields immediately after construction.

        Args:
            None (operates on the dataclass fields).

        Returns:
            None.

        Raises:
            TypeError: If any field has the wrong type (e.g. non-str tag,
                non-bool ``requires_emulation``, non-mapping ``runner_variables``).
            ValueError: If required string fields are empty or ``user_id`` <= 0.
        """
        require_non_empty_str(self.test_id, "test_id")
        require_positive_int(self.user_id, "user_id")
        require_non_empty_str(self.runner_type, "runner_type")
        require_non_empty_str(self.framework_image, "framework_image")
        if not isinstance(self.required_hw_tags, list) or not all(
            isinstance(t, str) for t in self.required_hw_tags
        ):
            raise TypeError("required_hw_tags must be a list[str]")
        if not isinstance(self.requires_emulation, bool):
            raise TypeError("requires_emulation must be a bool")
        if self.quali_reservation_id is not None:
            require_non_empty_str(self.quali_reservation_id, "quali_reservation_id")
        require_mapping(self.runner_variables, "runner_variables")

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize the bundle for transport over the broker.

        Args:
            None.

        Returns:
            Dict[str, Any]: All fields as a JSON-serializable mapping.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TestBundleRequest":
        """
        Rebuild and validate a bundle from a broker payload.

        Args:
            data: Mapping decoded from a Celery/AMQP message.

        Returns:
            TestBundleRequest: A validated instance.

        Raises:
            TypeError: If ``data`` is not a mapping (or contained fields have
                wrong types, via ``__post_init__``).
            ValueError: If required keys are missing or field values are invalid.
        """
        require_mapping(data, "bundle")
        require_keys(data, ["test_id", "user_id", "runner_type", "framework_image"], "bundle")
        return cls(
            test_id=data["test_id"],
            user_id=data["user_id"],
            runner_type=data["runner_type"],
            framework_image=data["framework_image"],
            required_hw_tags=list(data.get("required_hw_tags", [])),
            requires_emulation=bool(data.get("requires_emulation", False)),
            quali_reservation_id=data.get("quali_reservation_id"),
            runner_variables=dict(data.get("runner_variables", {})),
        )
