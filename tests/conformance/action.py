"""Action and semimodule laws shared by recognize-act implementations."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from tiergraph.action import ActionDeclaration, ReactDeclaration, Semimodule


@dataclass(frozen=True)
class ActionLawSuite:
    """Apply action laws through replaceable declaration factories."""

    react: Callable[[object], ReactDeclaration[object, object, object]]
    carriers: tuple[object, object]

    def check_carrier_substitution(self) -> None:
        """Recognition is carrier-independent because only action reads carriers."""
        left = self.react(self.carriers[0]).run(self.carriers[0])
        right = self.react(self.carriers[1]).run(self.carriers[1])

        def encode(value: dict[str, object]) -> bytes:
            """Encode only recognition with a canonical mapping order."""
            return json.dumps(
                value["recognition"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()

        assert encode(left) == encode(right)


@dataclass(frozen=True)
class SemimoduleLawSuite:
    """Execute the laws carried only by an explicit semimodule claim."""

    structure: Semimodule[object, object]

    def check_laws(self) -> None:
        """Check sampled additive structures and scalar compatibility."""
        law = self.structure
        for value in law.module_samples:
            assert law.module_add(value, law.module_zero) == value
            assert law.scale(law.scalar_one, value) == value
            assert law.scale(law.scalar_zero, value) == law.module_zero
            for left in law.module_samples:
                assert law.module_add(value, left) == law.module_add(left, value)
                for right in law.module_samples:
                    assert law.module_add(
                        law.module_add(value, left), right
                    ) == law.module_add(value, law.module_add(left, right))
        for scalar in law.scalar_samples:
            for left_scalar in law.scalar_samples:
                for right_scalar in law.scalar_samples:
                    assert law.scalar_add(
                        law.scalar_add(scalar, left_scalar), right_scalar
                    ) == law.scalar_add(
                        scalar, law.scalar_add(left_scalar, right_scalar)
                    )
                    assert law.scalar_multiply(
                        law.scalar_multiply(scalar, left_scalar), right_scalar
                    ) == law.scalar_multiply(
                        scalar, law.scalar_multiply(left_scalar, right_scalar)
                    )
        for scalar in law.scalar_samples:
            for left in law.module_samples:
                for right in law.module_samples:
                    assert law.scale(
                        scalar, law.module_add(left, right)
                    ) == law.module_add(
                        law.scale(scalar, left), law.scale(scalar, right)
                    )
        for left_scalar in law.scalar_samples:
            for right_scalar in law.scalar_samples:
                for value in law.module_samples:
                    assert law.scale(
                        law.scalar_add(left_scalar, right_scalar), value
                    ) == law.module_add(
                        law.scale(left_scalar, value),
                        law.scale(right_scalar, value),
                    )
                    assert law.scale(
                        law.scalar_multiply(left_scalar, right_scalar), value
                    ) == law.scale(left_scalar, law.scale(right_scalar, value))


@dataclass(frozen=True)
class ActionToleranceLawSuite:
    """Execute every normalization tolerance claimed by an action."""

    action: ActionDeclaration[object, object]
    carrier: object
    left: tuple[object, ...]
    right: tuple[object, ...]

    def check_claims(self) -> None:
        """Check only the laws the declaration says its action supports."""
        action = self.action
        if action.associative:
            successive = action.apply(action.apply(self.carrier, self.left), self.right)
            assert successive == action.apply(self.carrier, self.left + self.right)
        if action.idempotent:
            once = action.apply(self.carrier, self.left)
            assert action.apply(once, self.left) == once
        if action.commutative:
            assert action.apply(self.carrier, self.left + self.right) == action.apply(
                self.carrier, self.right + self.left
            )
