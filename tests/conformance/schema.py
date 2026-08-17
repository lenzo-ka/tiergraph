"""Schema laws shared by generated wire-contract implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tiergraph.core import JsonValue


@dataclass(frozen=True)
class SchemaLawSuite:
    """Apply schema laws through replaceable generation and validation boundaries."""

    schema_bytes: Callable[[], bytes]
    validate_fixture: Callable[[], list[str]]
    changed_schema_bytes: Callable[[], bytes]

    def check_fixture_validation(self) -> None:
        """Every codec fixture is admitted by declaration-derived validation."""
        assert self.validate_fixture() == []

    def check_generation_is_deterministic(self) -> None:
        """Repeated generation returns byte-identical schema data."""
        assert self.schema_bytes() == self.schema_bytes()

    def check_generation_observes_shape(self) -> None:
        """A changed declaration produces a changed generated artifact."""
        assert self.schema_bytes() != self.changed_schema_bytes()

    def check_schema_is_json_data(self, schema: dict[str, JsonValue]) -> None:
        """The generated public schema consists only of JSON values."""
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
