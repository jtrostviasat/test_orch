# shared

Cross-service code imported by **both** the backend and the worker. Keeping these
contracts in one place guarantees the two sides agree on payload shapes and
validation rules.

## Modules

| Module | Purpose |
| --- | --- |
| `validation.py` | `require_*` boundary guards that raise `ValueError`/`TypeError`. Safe under `python -O` (never `assert`). |
| `schemas.py` | `TestBundleRequest` (the job contract crossing RabbitMQ) and `TestStatus`. Self-validating on construction and on `from_dict`. |
| `host_stats.py` | `HostStats` dataclass + `collect_host_stats()` (psutil), with range-checked invariants and CPU clamping. |

## Validation philosophy

Two deliberate categories:

- **`require_*` functions → raise.** Use at trust boundaries: broker/AMQP
  payloads, LDAP/user input, env-derived values. These must survive `python -O`,
  so they never use `assert`.
- **`assert` statements → invariants.** Use for internal "this should be
  impossible" checks where being compiled out under `-O` is acceptable.

## Example

```python
from shared.schemas import TestBundleRequest

bundle = TestBundleRequest(
    test_id="t-123",
    user_id=42,
    runner_type="pytest",
    framework_image="registry.example.com/pytest-runner:1.4",
    required_hw_tags=["DUT_TYPE_A"],
    requires_emulation=False,
)
payload = bundle.to_dict()          # safe to publish on the broker
restored = TestBundleRequest.from_dict(payload)  # re-validates on the way in
```