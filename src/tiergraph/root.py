"""Profiles for ordered graph roots and persisted default choices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from tiergraph.core import (
    Graph,
    ItemRef,
    PolyadicRelationDeclaration,
    QualifiedName,
    RelationEndpointKind,
)


def _polyadic_declaration(
    graph: Graph, name: QualifiedName, role: str
) -> PolyadicRelationDeclaration:
    matches = tuple(
        declaration
        for declaration in graph.relation_declarations
        if declaration.name == name
    )
    if not matches or not isinstance(matches[0], PolyadicRelationDeclaration):
        raise ValueError(
            f"{role} relation {str(name)!r} requires a polyadic declaration"
        )
    return matches[0]


def _item_only(declaration: PolyadicRelationDeclaration, role: str) -> None:
    item_only = (RelationEndpointKind.ITEM,)
    if (
        declaration.sources.endpoint_kinds != item_only
        or declaration.targets.endpoint_kinds != item_only
    ):
        raise ValueError(
            f"{role} relation {str(declaration.name)!r} requires item-only sides"
        )


@dataclass(frozen=True, slots=True)
class OrderedRootsProfile:
    """Read ordered stored roots and reconcile them with dependency incidence.

    The root relation is one polyadic instance with an explicitly empty source
    side. Its target incidence order is the declared root order. Dependency
    relations determine root membership: every item on an admitted root tier
    with no incoming dependency incidence is a root. Stored order adds
    information, but stored membership may not contradict that derived set.
    """

    graph: Graph
    root_relation: QualifiedName
    dependency_relations: tuple[QualifiedName, ...]

    def __post_init__(self) -> None:
        """Refuse role declarations and stored roots that can drift."""
        root_declaration = _polyadic_declaration(
            self.graph, self.root_relation, "ordered-root"
        )
        _item_only(root_declaration, "ordered-root")
        sources = root_declaration.sources
        if not (sources.allow_empty and sources.minimum == 0 and sources.maximum == 0):
            raise ValueError(
                f"ordered-root relation {str(self.root_relation)!r} requires "
                "an explicitly empty source side"
            )
        if not root_declaration.distinct_targets:
            raise ValueError(
                f"ordered-root relation {str(self.root_relation)!r} requires "
                "distinct targets"
            )
        for dependency in self.dependency_relations:
            declaration = _polyadic_declaration(
                self.graph, dependency, "root dependency"
            )
            _item_only(declaration, "root dependency")

        instances = tuple(
            relation
            for relation in self.graph.polyadic_relations
            if relation.declaration == self.root_relation
        )
        if len(instances) != 1:
            raise ValueError(
                f"ordered-root relation {str(self.root_relation)!r} has "
                f"{len(instances)} instances; expected exactly one"
            )
        inferred = self.inferred()
        roots = self.roots()
        if set(roots) != set(inferred):
            raise ValueError(
                f"ordered-root relation {str(self.root_relation)!r} stored roots "
                f"{[item.to_data() for item in roots]!r} contradict inferred roots "
                f"{[item.to_data() for item in inferred]!r}"
            )

    def _domain(self) -> tuple[ItemRef, ...]:
        declaration = _polyadic_declaration(
            self.graph, self.root_relation, "ordered-root"
        )
        tiers = declaration.targets.tiers
        if tiers is None:
            return self.graph.canonical_items()
        admitted = set(tiers)
        return tuple(
            reference
            for reference in self.graph.canonical_items()
            if reference.tier in admitted
        )

    def inferred(self) -> tuple[ItemRef, ...]:
        """Return dependency roots in canonical item order."""
        domain = self._domain()
        admitted = set(domain)
        incoming = {reference: 0 for reference in domain}
        dependencies = set(self.dependency_relations)
        for relation in self.graph.polyadic_relations:
            if relation.declaration not in dependencies:
                continue
            for source in relation.sources:
                for target in relation.targets:
                    if source in admitted and target in admitted:
                        incoming[target] += 1
        return tuple(reference for reference in domain if incoming[reference] == 0)

    def roots(self) -> tuple[ItemRef, ...]:
        """Return roots in the stored semantic incidence order."""
        instance = next(
            relation
            for relation in self.graph.polyadic_relations
            if relation.declaration == self.root_relation
        )
        return cast(tuple[ItemRef, ...], instance.targets)


@dataclass(frozen=True, slots=True)
class PersistedChoiceProfile:
    """Read alternatives and optional persisted singleton defaults by source."""

    graph: Graph
    alternatives_relation: QualifiedName
    default_relation: QualifiedName

    def __post_init__(self) -> None:
        """Require the general relation constraints that define this role."""
        alternatives = _polyadic_declaration(
            self.graph, self.alternatives_relation, "alternatives"
        )
        default = _polyadic_declaration(
            self.graph, self.default_relation, "persisted-default"
        )
        _item_only(alternatives, "alternatives")
        _item_only(default, "persisted-default")
        if not alternatives.unique_sources or not alternatives.distinct_targets:
            raise ValueError(
                f"alternatives relation {str(self.alternatives_relation)!r} requires "
                "source uniqueness and distinct targets"
            )
        if not default.unique_sources or not default.distinct_targets:
            raise ValueError(
                f"persisted-default relation {str(self.default_relation)!r} requires "
                "source uniqueness and distinct targets"
            )
        if default.targets.minimum != 1 or default.targets.maximum != 1:
            raise ValueError(
                f"persisted-default relation {str(self.default_relation)!r} requires "
                "exactly one target"
            )
        if default.targets_subset_of != self.alternatives_relation:
            raise ValueError(
                f"persisted-default relation {str(self.default_relation)!r} must "
                f"declare targets_subset_of {str(self.alternatives_relation)!r}"
            )

    def candidates(self, source: ItemRef) -> tuple[ItemRef, ...]:
        """Return the source's candidates in stored incidence order."""
        for relation in self.graph.polyadic_relations:
            if (
                relation.declaration == self.alternatives_relation
                and source in relation.sources
            ):
                return cast(tuple[ItemRef, ...], relation.targets)
        return ()

    def default(self, source: ItemRef) -> ItemRef | None:
        """Return the persisted default for a source when one is stored."""
        for relation in self.graph.polyadic_relations:
            if (
                relation.declaration == self.default_relation
                and source in relation.sources
            ):
                target = relation.targets[0]
                assert isinstance(target, ItemRef)
                return target
        return None
