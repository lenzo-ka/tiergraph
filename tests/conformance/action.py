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
        """Recheck the construction-time sampled semimodule validation."""
        assert self.action.semimodule is not None, (
            f"action {self.action.name!r} makes no semimodule claim"
        )
        self.action._validate_semimodule_claim()


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
