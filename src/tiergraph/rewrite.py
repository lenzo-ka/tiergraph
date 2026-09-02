"""Declare what one graph-to-graph rewrite did to the graph it rewrote.

A rewrite here is any pair of graphs read as before and after. This module
produces no graphs and edits nothing: it measures the relation between two
graph values and holds a declaration about that relation to account.

"Tiers can only decorate" is not a law of this kernel, and stating it as one
would be false. A graph is an immutable value, so every edit already outputs a
new graph, and nothing stops that new graph from standing in any relation at
all to the old one. What is true, and worth declaring, is that a *particular*
rewrite decorated -- and that is a claim a caller can make and this module can
refute.

The three effects are ordered by how much they disturb what they rewrote:

``DECORATE``
    The result adds and takes nothing back. Every structure the source asserts
    stands in the result at the same coordinate, carrying everything it
    carried; the result may carry more.
``REVISE``
    Every structure still stands, but somewhere a value stands in place of
    another the source carried.
``COLLAPSE``
    Some structure the source asserts has no counterpart in the result at all.

``DECORATE`` is the strongest and the one that licenses the most. Its license
is stated in ``RewriteDeclaration.check_effect``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

from tiergraph.core import (
    AttributeValue,
    BoundaryRef,
    Graph,
    ItemRef,
    JsonValue,
    LayerSubject,
    QualifiedName,
    TierRef,
)


class RewriteEffect(Enum):
    """State what a rewrite did to the graph it rewrote.

    ``DECORATE``
        The rewrite added to the source and took nothing back. Gate: no
        structure the source asserts may be missing from the result, and no
        value it carries may have been replaced.
    ``REVISE``
        Every structure the source asserts still stands, but some value stands
        in place of another. Gate: the replacement must be exhibitable -- a
        ``REVISE`` claim over a rewrite that replaced nothing is a declaration
        that is hiding, and it is refused.
    ``COLLAPSE``
        Some structure the source asserts is gone. Gate: the loss must be
        exhibitable, for the same reason.
    ``UNDECLARED``
        The default. It is refused, and it does not mean ``COLLAPSE``:
        declining to say is not the same as saying the weaker thing, and the
        refusal says so by handing back the declaration to be made.

    Every branch bites, and the asymmetry is deliberate. Omitting the claim is
    answered with the declaration; asserting it falsely is answered with a
    semantic counterexample naming the structure and what happened to it.
    """

    DECORATE = "decorate"
    REVISE = "revise"
    COLLAPSE = "collapse"
    UNDECLARED = "undeclared"


class EffectRefusal(ValueError):
    """Refuse an effect claim a rewrite does not make good on."""


@dataclass(frozen=True, slots=True)
class RewriteDisturbance:
    """Name one structure the rewrite did not leave standing as it found it.

    ``effect`` is ``REVISE`` when the structure still stands and a value in it
    was replaced, and ``COLLAPSE`` when the structure or the value is gone.
    ``subject`` names it in the source's own coordinates, ``tier`` is the tier
    it belongs to when it belongs to one, and ``detail`` says what happened.
    """

    effect: RewriteEffect
    subject: str
    tier: QualifiedName | None
    detail: str

    def to_data(self) -> dict[str, JsonValue]:
        """Return the disturbance as JSON-serializable data."""
        return {
            "effect": self.effect.value,
            "subject": self.subject,
            "tier": None if self.tier is None else self.tier.to_data(),
            "detail": self.detail,
        }

    def __str__(self) -> str:
        """Render the disturbance as one diagnostic clause."""
        return f"{self.subject} {self.detail}"


@dataclass(frozen=True, slots=True)
class RewriteCertificate:
    """Report what discharged one rewrite's effect claim, and over how much.

    ``subjects`` is the honest part. It counts the structures the source
    asserts, every one of which was examined. A ``DECORATE`` claim over a
    source that asserts three things has been held to three things; the count
    is there so a nearly vacuous claim cannot be read as a strong one.

    ``disturbances`` counts the ways the result failed to leave the source's
    structures standing, which is zero exactly when the rewrite decorated. One
    structure contributes one entry per way, so this is not a count of
    structures and does not sit on the same scale as ``subjects``.
    """

    effect: RewriteEffect
    subjects: int
    disturbances: int

    def to_data(self) -> dict[str, JsonValue]:
        """Return deterministic strict-JSON data.

        All three fields are carried because no two of them recover the third.
        ``effect`` is what was discharged, and it is the only one that separates
        a ``REVISE`` from a ``COLLAPSE``: both leave disturbances behind, so a
        certificate reporting counts alone would read identically for either.
        ``subjects`` is how much the claim was held to, without which a nearly
        vacuous discharge reads as a strong one. ``disturbances`` is what the
        check found, which is zero exactly when the rewrite decorated.

        ``disturbances`` is written as the count this type holds, under the name
        it holds it by, because a count of ways is what was measured: one
        structure that lost two attributes contributes two. It is therefore not
        a count of structures, it does not sit on the same scale as
        ``subjects``, and the two together are not a proportion of anything. A
        reader wanting the structures themselves calls
        ``RewriteDeclaration.disturbances()``, whose entries serialize through
        ``RewriteDisturbance.to_data``; this certificate says how far the check
        reached rather than what it saw.
        """
        return {
            "effect": self.effect.value,
            "subjects": self.subjects,
            "disturbances": self.disturbances,
        }


@dataclass(frozen=True, slots=True)
class RewriteDeclaration:
    """Bind one named claim to the pair of graphs a rewrite read and wrote.

    ``effect`` states what the rewrite did to ``source``. It defaults to
    ``UNDECLARED`` and nothing consults it until ``check_effect()`` is called,
    because the claim is owed where it is relied on rather than where a pair of
    graphs is built.

    This is a claim about two graph *values*. It does not know, and does not
    ask, whether one was produced from the other: two graphs built
    independently that happen to stand in this relation are measured exactly as
    a rewrite and its input would be.
    """

    name: str
    source: Graph
    result: Graph
    effect: RewriteEffect = RewriteEffect.UNDECLARED

    def __post_init__(self) -> None:
        """Refuse a claim with no name to report against."""
        if not self.name:
            raise ValueError("rewrite name '' must not be empty")

    def disturbances(self) -> tuple[RewriteDisturbance, ...]:
        """Return every way the rewrite disturbed a structure, in source order.

        The order is the source graph's own reading order -- namespaces, then
        each tier and its items, then relation declarations, attribute
        declarations, relation instances, polyadic relation instances, boundary
        values, each layer and the facts it holds, and the document. It is
        total and reproducible, so the first disturbance is the first in a
        fixed order rather than a minimized or a most-severe one, and the
        refusals report it as such.
        """
        after = {fact.key: fact for fact in _facts(self.result)}
        return tuple(
            disturbance
            for fact in _facts(self.source)
            for disturbance in fact.against(after.get(fact.key))
        )

    def check_effect(self) -> RewriteCertificate:
        """Demand this rewrite's effect claim and discharge it, or refuse.

        Every branch bites, and the asymmetry is deliberate. An ``UNDECLARED``
        effect is refused with **the declaration to be made**; a false claim is
        refused with **a semantic counterexample** naming the structure, the
        tier it belongs to, and what happened to it. Declining to say is not
        the same as saying the weaker thing.

        What a discharged ``DECORATE`` licenses is one thing and not more:
        every reading taken over the source is still a correct reading of the
        result, without re-reading it. An item's attributes, a boundary's
        values, a relation's endpoints, whatever a reference resolved to --
        all of it still holds. What it does not license is any reading that
        counts, quantifies over everything, or turns on absence: a tier's
        extent, a root set's exhaustiveness, the canonical bytes, the
        construction fingerprint. Decoration adds, so those must be taken
        again. Put shortly, a positive property proved of the source transfers
        to the result and a negative or counting one does not.

        As this tree stands that license discharges a proof obligation and buys
        no optimization: nothing here caches a reading across a rewrite, so
        there is no revalidation for the claim to skip. It is stated as a
        license rather than a speedup on purpose.
        """
        if self.effect is RewriteEffect.UNDECLARED:
            raise EffectRefusal(
                f"rewrite {self.name!r} effect is UNDECLARED: say what this "
                "rewrite did to the graph it rewrote -- added to it and took "
                "nothing back (DECORATE), left every structure standing but "
                "put some value in place of another (REVISE), or left some "
                "structure of the source with no counterpart at all "
                "(COLLAPSE). Not declaring is not the same as declaring "
                "COLLAPSE."
            )
        found = self.disturbances()
        observed = _worst(found)
        subjects = sum(1 for _ in _facts(self.source))
        if observed is not self.effect:
            raise EffectRefusal(self._refusal(observed, found, subjects))
        return RewriteCertificate(self.effect, subjects, len(found))

    def _refusal(
        self,
        observed: RewriteEffect,
        found: tuple[RewriteDisturbance, ...],
        subjects: int,
    ) -> str:
        """Render the counterexample, or the claim that cannot be exhibited."""
        if observed is RewriteEffect.DECORATE:
            act, noun = (
                ("replaced", "revision")
                if self.effect is RewriteEffect.REVISE
                else ("lost", "collapse")
            )
            return (
                f"rewrite {self.name!r} declares {self.effect.name}, but every "
                f"one of the {subjects} structures the source asserts stands "
                "in the result carrying everything it carried, so nothing was "
                f"{act}. A {noun} you cannot exhibit is a declaration that is "
                "hiding. Declare DECORATE."
            )
        witness = next(item for item in found if item.effect is observed)
        others = len(found) - 1
        if others == 0:
            also = ""
        elif others == 1:
            also = " 1 further disturbance also applies."
        else:
            also = f" {others} further disturbances also apply."
        return (
            f"rewrite {self.name!r} declares {self.effect.name}, but "
            f"{witness}. {_BECAUSE[self.effect, observed]} Declare "
            f"{observed.name}. The disturbance "
            "reported is the first in the source's own reading order rather "
            f"than the only one or the worst one.{also}"
        )


# Why the declared effect cannot be what the two graphs exhibit, for each pair
# a refusal can reach. Every entry contradicts the *declared* effect with the
# finding, rather than restating the finding, so the reader learns which half of
# their claim was wrong.
_BECAUSE: dict[tuple[RewriteEffect, RewriteEffect], str] = {
    (RewriteEffect.DECORATE, RewriteEffect.REVISE): (
        "A rewrite that decorates adds to what it rewrote and takes nothing "
        "back, so a value standing where another stood is not decoration."
    ),
    (RewriteEffect.DECORATE, RewriteEffect.COLLAPSE): (
        "A rewrite that decorates adds to what it rewrote and takes nothing "
        "back, so a structure with no counterpart is not decoration."
    ),
    (RewriteEffect.REVISE, RewriteEffect.COLLAPSE): (
        "A rewrite that revises leaves every structure standing, so a "
        "structure with no counterpart is not a revision."
    ),
    (RewriteEffect.COLLAPSE, RewriteEffect.REVISE): (
        "A rewrite that collapses leaves some structure of the source with no "
        "counterpart, and every one of them still stands here."
    ),
}


def _worst(found: tuple[RewriteDisturbance, ...]) -> RewriteEffect:
    """Return the most disturbing effect exhibited, or ``DECORATE`` if none is."""
    if any(item.effect is RewriteEffect.COLLAPSE for item in found):
        return RewriteEffect.COLLAPSE
    if found:
        return RewriteEffect.REVISE
    return RewriteEffect.DECORATE


@dataclass(frozen=True, slots=True)
class _Fact:
    """One structure a graph asserts, with everything it carries.

    ``core`` holds named values that must be equal for the structure to be the
    same structure. ``identity`` is the durable-identity seam, which extends
    from absent to present and no further. ``attributes`` holds the typed
    values carried, which a decoration may add to but not replace.
    """

    key: tuple[str, ...]
    subject: str
    tier: QualifiedName | None
    core: tuple[tuple[str, str], ...]
    identity: str | None = None
    attributes: tuple[tuple[str, str], ...] = ()

    def against(self, other: _Fact | None) -> Iterator[RewriteDisturbance]:
        """Yield every way the result failed to leave this structure standing."""
        if other is None:
            yield self._collapse("has no counterpart in the result")
            return
        for (field, mine), (_, theirs) in zip(self.core, other.core, strict=True):
            if mine != theirs:
                yield self._revise(
                    f"carries {field} {theirs!r} where the source carried {mine!r}"
                )
        if self.identity is not None and self.identity != other.identity:
            if other.identity is None:
                yield self._collapse(f"no longer carries durable id {self.identity!r}")
            else:
                yield self._revise(
                    f"carries durable id {other.identity!r} where the source "
                    f"carried {self.identity!r}"
                )
        carried = dict(other.attributes)
        for name, value in self.attributes:
            if name not in carried:
                yield self._collapse(f"no longer carries attribute {name!r}")
            elif carried[name] != value:
                yield self._revise(
                    f"carries attribute {name!r} as {carried[name]!r} where "
                    f"the source carried {value!r}"
                )

    def _collapse(self, detail: str) -> RewriteDisturbance:
        """Report this structure or one of its values as gone."""
        return RewriteDisturbance(
            RewriteEffect.COLLAPSE, self.subject, self.tier, detail
        )

    def _revise(self, detail: str) -> RewriteDisturbance:
        """Report a value standing where another value stood."""
        return RewriteDisturbance(RewriteEffect.REVISE, self.subject, self.tier, detail)


def _attributes(values: tuple[AttributeValue, ...]) -> tuple[tuple[str, str], ...]:
    """Key each carried value by its expanded name, with type and lexical."""
    return tuple(
        (str(value.name), f"{value.value_type.value}:{value.lexical}")
        for value in values
    )


def _shape(data: dict[str, JsonValue]) -> str:
    """Render a declaration's identifying content apart from what it carries."""
    return json.dumps(
        {key: value for key, value in data.items() if key != "attributes"},
        sort_keys=True,
    )


def _facts(graph: Graph) -> Iterator[_Fact]:
    """Yield every structure the graph asserts, in the graph's reading order."""
    for binding in graph.namespaces:
        yield _Fact(
            ("namespace", binding.prefix),
            f"namespace prefix {binding.prefix!r}",
            None,
            (("namespace URI", binding.namespace),),
        )
    for tier in graph.tiers:
        name = tier.declaration.name
        yield _Fact(
            ("tier", str(name)),
            f"tier {str(name)!r}",
            name,
            (("long name", tier.declaration.long_name),),
            attributes=_attributes(tier.attributes),
        )
        for index, item in enumerate(tier.items):
            yield _Fact(
                ("item", str(name), str(index)),
                f"item {str(name)!r}[{index}]",
                name,
                (),
                item.durable_id,
                _attributes(item.attributes),
            )
    for declaration in graph.relation_declarations:
        yield _Fact(
            ("relation-declaration", str(declaration.name)),
            f"relation declaration {str(declaration.name)!r}",
            None,
            (("shape", _shape(declaration.to_data())),),
            attributes=_attributes(declaration.attributes),
        )
    for attribute in graph.attribute_declarations:
        yield _Fact(
            ("attribute-declaration", str(attribute.name)),
            f"attribute declaration {str(attribute.name)!r}",
            None,
            (("shape", _shape(attribute.to_data())),),
        )
    for index, relation in enumerate(graph.relations):
        yield _Fact(
            ("relation", str(index)),
            f"relation instance {index}",
            None,
            (("endpoints", _shape(relation.to_data())),),
            relation.durable_id,
            _attributes(relation.attributes),
        )
    for index, polyadic in enumerate(graph.polyadic_relations):
        yield _Fact(
            ("polyadic-relation", str(index)),
            f"polyadic relation instance {index}",
            None,
            (("endpoints", _shape(polyadic.to_data())),),
            polyadic.durable_id,
            _attributes(polyadic.attributes),
        )
    for boundary in graph.boundary_values:
        coordinate = graph.resolve_boundary(boundary.reference)
        yield _Fact(
            ("position", str(coordinate.tier), str(coordinate.index)),
            f"boundary {str(coordinate.tier)!r}[{coordinate.index}]",
            coordinate.tier,
            (),
            attributes=_attributes(boundary.attributes),
        )
    for layer in graph.layers:
        vocabulary, layer_source = layer.name.vocabulary, layer.name.source
        yield _Fact(
            ("layer", vocabulary, layer_source),
            f"layer {vocabulary!r}/{layer_source!r}",
            None,
            (),
        )
        for fact in layer.facts:
            subject = str(fact.subject)
            yield _Fact(
                (
                    "layer-fact",
                    vocabulary,
                    layer_source,
                    subject,
                    str(fact.value.name),
                ),
                f"layer {vocabulary!r}/{layer_source!r} statement "
                f"{str(fact.value.name)!r} at {subject}",
                _layer_fact_tier(fact.subject),
                (
                    (
                        "statement",
                        f"{fact.value.value_type.value}:{fact.value.lexical}",
                    ),
                ),
            )
    yield _Fact(
        ("document",),
        "the document",
        None,
        (),
        attributes=_attributes(graph.attributes),
    )


def _layer_fact_tier(subject: LayerSubject) -> QualifiedName | None:
    """Name the tier a layer statement stands over, where it stands over one."""
    if isinstance(subject, ItemRef | BoundaryRef | TierRef):
        return subject.tier
    return None


__all__ = [
    "EffectRefusal",
    "RewriteCertificate",
    "RewriteDeclaration",
    "RewriteDisturbance",
    "RewriteEffect",
]
