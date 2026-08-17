"""Action and semimodule laws shared by recognize-act implementations."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace

from tiergraph.action import (
    ActionDeclaration,
    ReactDeclaration,
    ReactMode,
)


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

    def check_one_for_one_equivalence(self) -> None:
        """Require every supplied carrier to agree in per-item and batch modes."""
        for carrier in self.carriers:
            declaration = self.react(carrier)
            transactional = declaration.run(carrier)
            one_for_one = replace(
                declaration,
                mode=ReactMode.ONE_FOR_ONE,
                distribution=None,
            ).run(carrier)
            assert one_for_one["result"] == transactional["result"], (
                f"react {declaration.name!r} one-for-one result differs from "
                f"transactional result for action {declaration.action.name!r}"
            )


@dataclass(frozen=True)
class SemimoduleLawSuite:
    """Check a claimed semimodule's axioms and its bound action."""

    action: ActionDeclaration[object, object]

    def check_laws(self) -> None:
        """Check sampled additive structures and scalar compatibility."""
        law = self.action.semimodule
        assert law is not None, f"action {self.action.name!r} makes no semimodule claim"
        assert law.module_add(law.module_zero, law.module_zero) == law.module_zero
        assert law.scale(law.scalar_one, law.module_zero) == law.module_zero
        for value in law.module_samples:
            assert law.module_add(value, law.module_zero) == value
            assert law.module_add(law.module_zero, value) == value
            assert law.scale(law.scalar_one, value) == value
            assert law.scale(law.scalar_zero, value) == law.module_zero
            for left in law.module_samples:
                assert law.module_add(value, left) == law.module_add(left, value)
                for right in law.module_samples:
                    assert law.module_add(
                        law.module_add(value, left), right
                    ) == law.module_add(value, law.module_add(left, right))
        for scalar in law.scalar_samples:
            assert law.scalar_add(scalar, law.scalar_zero) == scalar
            assert law.scalar_add(law.scalar_zero, scalar) == scalar
            assert law.scalar_multiply(scalar, law.scalar_one) == scalar
            assert law.scalar_multiply(law.scalar_one, scalar) == scalar
            assert law.scalar_multiply(scalar, law.scalar_zero) == law.scalar_zero
            assert law.scalar_multiply(law.scalar_zero, scalar) == law.scalar_zero
            assert law.scale(scalar, law.module_zero) == law.module_zero
            for value in law.module_samples:
                expected = law.scale(scalar, value)
                try:
                    actual = self.action.apply(value, (scalar,))
                except Exception as error:
                    raise AssertionError(
                        f"action {self.action.name!r} does not implement its "
                        f"semimodule scale for {scalar!r}, {value!r}"
                    ) from error
                assert actual == expected, (
                    f"action {self.action.name!r} does not implement its semimodule "
                    f"scale for {scalar!r}, {value!r}: {actual!r} != {expected!r}"
                )
            for left_scalar in law.scalar_samples:
                assert law.scalar_add(scalar, left_scalar) == law.scalar_add(
                    left_scalar, scalar
                )
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
                    assert law.scalar_multiply(
                        scalar, law.scalar_add(left_scalar, right_scalar)
                    ) == law.scalar_add(
                        law.scalar_multiply(scalar, left_scalar),
                        law.scalar_multiply(scalar, right_scalar),
                    )
                    assert law.scalar_multiply(
                        law.scalar_add(left_scalar, right_scalar), scalar
                    ) == law.scalar_add(
                        law.scalar_multiply(left_scalar, scalar),
                        law.scalar_multiply(right_scalar, scalar),
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
