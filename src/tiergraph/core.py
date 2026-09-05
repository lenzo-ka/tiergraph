"""Immutable declarations and graph values for ordered parallel structure."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import IntEnum, StrEnum
from functools import total_ordering
from types import MappingProxyType
from typing import NamedTuple, Protocol, cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_INTEGER_LEXICAL = re.compile(r"[+-]?[0-9]+\Z")
_DECIMAL_LEXICAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_DOUBLE_LEXICAL = re.compile(
    r"(?:NaN|[+-]?INF|[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?))\Z"
)


class RefusalStage(IntEnum):
    """Number the classes a refusal can belong to, lowest reported first.

    A reader routinely meets several conditions at once.  The stage numbers put
    them in one order, so a caller is told the condition that explains the rest
    rather than whichever check happened to run first: a refusal at one stage
    explains what a later stage would have reported, and the converse never
    holds.  Bytes that are not text have no JSON to nest; a document announcing
    a format this release does not implement has a field set this release cannot
    judge; a member of the wrong construction has no value to place in a
    declared language; a name that does not resolve cannot keep a promise.

    The stages rank the conditions that apply to one node.  Nodes are read from
    the outside in and members in their declared order, so an enclosing node's
    condition precedes its members' whatever their stages, and the pair of a
    node and a stage totally orders every condition one read can meet.

    A condition is carried beside the primary one only while it stays
    applicable once the primary is known.  A field set is not judged against a
    declaration the document never selected, so a foreign version is reported
    alone rather than with the fields that being foreign introduces.

    The stage is the stable part of a refusal; the wording is diagnostic.

    The vocabulary lives here, beside the other declared enumerations, because
    both refusal channels have to name it: this module is the base every other
    imports, so a refusal raised from here can carry a stage without the cycle
    that reaching upward for it would create.
    """

    ENVELOPE = 1
    ENCODING = 2
    SYNTAX = 3
    CONSTRUCTION = 4
    DISCRIMINATOR = 5
    SHAPE = 6
    VALUE = 7
    REFERENCE = 8
    SEMANTICS = 9


class Refusal(ValueError):
    """Refuse one read, naming its stage and applicable conditions at its site.

    ``stage`` places the refusal in the declared total order, and ``also``
    carries the further conditions applicable at the refusing site, each a
    refusal in its own right.  It is not a census of the document.  Both are
    data rather than prose, so a caller acts on the order without matching
    message text.  Both are declared on the class as well as assigned, so a
    caller reads them as fields of what it caught rather than recovering them
    with ``getattr``.

    This is the one base every staged refusal has.  Wherever the order is
    observed it is observed whole, so ``except Refusal`` has to catch all of it:
    a base that covered a prefix of the order would send a caller who read the
    declaration past the ranks it left out.  Which readers observe the order,
    and where one of them answers unstaged instead, is stated in the format
    document rather than here -- this base is about the ranks a caller must be
    able to catch, not about which readers produce them.  Subclasses say which
    channel refused, never which ranks a caller has to expect.

    A ``Refusal`` is a ``ValueError``, so every caller that already catches one
    still does.

    Not every refusal this package raises is staged, and the boundary is worth
    stating because ``except Refusal`` is silent on the other side of it.  A
    *declaration* refuses its own construction with a plain ``ValueError`` --
    ``SealDeclaration``, ``FoldDeclaration``, ``AttributeValuation``,
    ``ActionDeclaration`` and ``ReactDeclaration`` all refuse an empty name that
    way, and ``DistributionWitness`` refuses its own the same way.
    Those are refusals about the description a caller wrote, not about a
    document or a graph, so there is no read for a stage to rank them within.
    What carries a stage is the refusal of *content*: a document a reader
    refuses, and a graph ``GraphValidationError`` refuses at construction or
    validation.  A caller that wants both catches ``ValueError``.

    ``tiergraph.Refusal`` is the staged document-reader refusal.  The other
    exported classes ending in ``Refusal`` -- ``StarRefusal``,
    ``EffectRefusal``, ``ExactnessRefusal``, ``PathRefusal``, and
    ``ProfileRegistrationRefusal`` -- are ``ValueError`` subclasses carrying
    their own subsystem's data.  They have no document-reader stage.

    It is declared here, beside ``RefusalStage`` and for the same reason: this
    module is the base every other imports, so the channel that refuses from
    here can share the base without the cycle that reaching upward would create.
    """

    stage: RefusalStage
    also: tuple[Refusal, ...]

    def __init__(
        self,
        stage: RefusalStage,
        message: str,
        also: Iterable[Refusal] = (),
    ) -> None:
        """Record the stage and the further conditions that still apply."""
        super().__init__(message)
        self.stage = stage
        self.also = tuple(also)


class GraphValidationError(Refusal):
    """Report a declaration or graph-contract validation failure.

    A caller meets refusals from two channels and should have to learn one
    vocabulary, so this failure ranks in the same order under the same base, and
    carries its ``stage`` as data rather than prose.  The stage defaults to
    ``SEMANTICS`` because a violated declaration or graph contract is semantic
    by nature: the document parsed, its shapes held, and what it says is still
    not sayable.  Every raise site in this package takes that default; a site
    whose condition is sharper may name one, and the argument is kept for that
    reason and for a caller constructing one of these itself.  The message stays
    first so an existing raise reads unchanged.

    A graph contract is one condition about the whole graph rather than a node
    whose siblings are still judged, so ``also`` is empty here.

    This is still a ``ValueError``, so every caller that already catches one
    still does.
    """

    def __init__(
        self,
        message: str,
        stage: RefusalStage = RefusalStage.SEMANTICS,
    ) -> None:
        """Record the stage this failure belongs to beside its wording."""
        super().__init__(stage, message)


class AttributeDomain(StrEnum):
    """The closed set of places where a declared attribute may occur."""

    ITEM = "item"
    TIER = "tier"
    RELATION_DECLARATION = "relation_declaration"
    RELATION_INSTANCE = "relation_instance"
    BOUNDARY = "boundary"
    DOCUMENT = "document"


class LayerRead(StrEnum):
    """Choose how a delivery answers a subject several layers describe."""

    FIRST = "first"
    LAST = "last"
    ALL = "all"


class XsdType(StrEnum):
    """The growable XSD datatype subset admitted for scalar attribute values.

    In-graph references are relations, not attribute value types.  Relation
    declarations type their referents and may validate structural promises;
    an out-of-graph reference is honestly a string because this graph cannot
    validate what it denotes.
    """

    STRING = "string"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DOUBLE = "double"


class BoundarySide(StrEnum):
    """Choose the boundary immediately before or after an anchor."""

    BEFORE = "before"
    AFTER = "after"


class RelationEndpointKind(StrEnum):
    """Declare whether one relation endpoint is an item or a boundary."""

    ITEM = "item"
    BOUNDARY = "boundary"


@dataclass(frozen=True, slots=True, order=True)
class QualifiedName:
    """Identify a declaration by namespace URI and local name."""

    namespace: str
    local_name: str

    def __post_init__(self) -> None:
        """Require both parts because an unqualified name is a different contract."""
        _require_name(self.namespace, "qualified-name namespace")
        _require_name(self.local_name, "qualified-name local name")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the expanded name independently of document prefix choices."""
        return {"namespace": self.namespace, "local_name": self.local_name}

    def __str__(self) -> str:
        """Return a compact expanded spelling for diagnostics."""
        return f"{{{self.namespace}}}{self.local_name}"


@dataclass(frozen=True, slots=True, order=True)
class LayerName:
    """Identify a layer by its vocabulary and its producing source."""

    vocabulary: str
    source: str

    def __post_init__(self) -> None:
        _require_name(self.vocabulary, "layer vocabulary")
        _require_name(self.source, "layer source")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the layer identity axes as JSON-serializable data."""
        return {"vocabulary": self.vocabulary, "source": self.source}


@dataclass(frozen=True, slots=True)
class TierRef:
    """Identify one tier as an attribute subject."""

    tier: QualifiedName


@dataclass(frozen=True, slots=True)
class RelationDeclarationRef:
    """Identify one relation declaration as an attribute subject."""

    relation: QualifiedName


@dataclass(frozen=True, slots=True)
class RelationInstanceRef:
    """Identify one binary relation instance by structural index."""

    index: int


@dataclass(frozen=True, slots=True)
class DurableRelationRef:
    """Identify one relation instance by durable identity."""

    durable_id: str


@dataclass(frozen=True, slots=True)
class PolyadicInstanceRef:
    """Identify one polyadic relation instance by structural index."""

    index: int


@dataclass(frozen=True, slots=True)
class DurablePolyadicRef:
    """Identify one polyadic relation instance by durable identity."""

    durable_id: str


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """Identify the document itself as an attribute subject."""


class GraphCarrier(StrEnum):
    """Name the graph's ordered carriers that are not a tier's items."""

    RELATIONS = "relations"
    POLYADIC_RELATIONS = "polyadic_relations"


type SealedCarrier = QualifiedName | GraphCarrier


@total_ordering
@dataclass(frozen=True, slots=True)
class Seal:
    """State how much of one ordered carrier may not be disturbed."""

    carrier: SealedCarrier
    sealed: int

    def __lt__(self, other: object) -> bool:
        """Order tier carriers before graph-wide carriers, then by prefix length."""
        if not isinstance(other, Seal):
            return NotImplemented
        return (*_sealed_carrier_key(self.carrier), self.sealed) < (
            *_sealed_carrier_key(other.carrier),
            other.sealed,
        )

    def to_data(self) -> dict[str, JsonValue]:
        """Return the tagged carrier and sealed prefix for wire encoding."""
        return {"carrier": _carrier_data(self.carrier), "sealed": self.sealed}


def _carrier_data(carrier: SealedCarrier) -> dict[str, JsonValue]:
    """Encode one tagged tier or graph carrier."""
    if isinstance(carrier, QualifiedName):
        return {"kind": "tier", "tier": carrier.to_data()}
    return {"kind": "graph", "name": carrier.value}


@dataclass(frozen=True, slots=True)
class SealBreach:
    """Name one sealed member that the result did not leave where it stood."""

    carrier: SealedCarrier
    index: int
    detail: str


@dataclass(frozen=True, slots=True)
class SealCertificate:
    """Report what a seal check could discriminate, and over how much.

    ``sealed_members`` counts only members whose durable identity made a
    value-only comparison capable of detecting movement. Anonymous members do
    not contribute: two graph values cannot reveal whether one anonymous member
    moved or an indistinguishable one took its coordinate. A zero count is
    therefore an explicit vacuous pass, not evidence that anonymous geometry was
    preserved.
    """

    carriers: int
    sealed_members: int

    def to_data(self) -> dict[str, int]:
        """Return deterministic strict-JSON data.

        Both counts are carried because either alone misleads. ``carriers``
        without ``sealed_members`` hides a vacuous pass; ``sealed_members``
        without ``carriers`` hides how much was under seal to begin with. A
        reader deciding what this certificate is worth needs the ratio, not
        either half.
        """
        return {"carriers": self.carriers, "sealed_members": self.sealed_members}


@dataclass(frozen=True, slots=True)
class NamespaceDeclaration:
    """Bind a document-local prefix to one namespace URI."""

    prefix: str
    namespace: str

    def __post_init__(self) -> None:
        """Require a prefix that is usable and writable, and a namespace URI.

        A colon in the prefix is refused here rather than at emission, because
        the colon is the qualified-name delimiter in every wire spelling: such a
        binding is unwritable in principle, not merely unwritten by this codec.
        """
        _require_name(self.prefix, "namespace prefix")
        if ":" in self.prefix:
            raise GraphValidationError(
                f"namespace prefix {self.prefix!r} must not contain ':'"
            )
        _require_name(self.namespace, f"namespace prefix {self.prefix!r}")

    @property
    def name(self) -> str:
        """Return the prefix used as the declaration key."""
        return self.prefix

    def to_data(self) -> dict[str, JsonValue]:
        """Return the prefix binding as JSON-serializable data."""
        return {"prefix": self.prefix, "namespace": self.namespace}


@dataclass(frozen=True, slots=True)
class AttributeValue:
    """Carry one named typed value in its XSD canonical lexical form."""

    name: QualifiedName
    value_type: XsdType
    lexical: str

    def __post_init__(self) -> None:
        """Separate lexical validation from the canonical spelling stored afterward."""
        try:
            canonical = _canonical_lexical(self.value_type, self.lexical)
        except ValueError as error:
            raise GraphValidationError(
                f"attribute {str(self.name)!r} has invalid {self.value_type.value} "
                f"value {self.lexical!r}"
            ) from error
        object.__setattr__(self, "lexical", canonical)

    def to_data(self) -> dict[str, JsonValue]:
        """Use lexical strings so every XSD value remains valid JSON."""
        return {
            "name": self.name.to_data(),
            "value_type": self.value_type.value,
            "lexical": self.lexical,
        }


@dataclass(frozen=True, slots=True)
class TierDeclaration:
    """Name an ordered tier without coupling its name to item identity."""

    name: QualifiedName
    long_name: str

    def __post_init__(self) -> None:
        """Require a long display name while identity stays in the qualified name."""
        _require_name(self.long_name, f"tier {str(self.name)!r} long name")

    @property
    def short_name(self) -> str:
        """Return the local part used as the tier's short display name."""
        return self.name.local_name

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {"name": self.name.to_data(), "long_name": self.long_name}


@dataclass(frozen=True, slots=True)
class AttributeDeclaration:
    """Declare an optional, at-most-one value for one domain and XSD type.

    Absence means absent: attributes have no defaults, deliberately, because a
    default would put a value in the reading that is missing from graph bytes.
    """

    name: QualifiedName
    domain: AttributeDomain
    value_type: XsdType

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "name": self.name.to_data(),
            "domain": self.domain.value,
            "value_type": self.value_type.value,
        }


@dataclass(frozen=True, slots=True)
class SimpleRelationDeclaration:
    """Give every member of one tier its type through a depth-one relation."""

    name: QualifiedName
    tier: QualifiedName
    item_type: QualifiedName
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize declaration attributes by their qualified names."""
        _canonicalize_attributes(self)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "kind": "simple",
            "name": self.name.to_data(),
            "tier": self.tier.to_data(),
            "item_type": self.item_type.to_data(),
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class BipartiteRelationDeclaration:
    """Declare typed links and the graph invariants they promise.

    Unlike scalar ``XsdType`` values, a relation types its referents through
    ``left_type`` and ``right_type`` and validates its ``single_parent`` and
    ``acyclic`` promises.
    """

    name: QualifiedName
    left_type: QualifiedName
    right_type: QualifiedName
    left_endpoint: RelationEndpointKind = RelationEndpointKind.ITEM
    right_endpoint: RelationEndpointKind = RelationEndpointKind.ITEM
    single_parent: bool = False
    acyclic: bool = False
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and require JSON-boolean promises."""
        _canonicalize_attributes(self)
        _require_boolean(self.single_parent, "single-parent promise")
        _require_boolean(self.acyclic, "acyclic promise")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "kind": "bipartite",
            "name": self.name.to_data(),
            "left_type": self.left_type.to_data(),
            "right_type": self.right_type.to_data(),
            "left_endpoint": self.left_endpoint.value,
            "right_endpoint": self.right_endpoint.value,
            "single_parent": self.single_parent,
            "acyclic": self.acyclic,
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class RelationSideDeclaration:
    """Constrain one explicitly ordered side of a polyadic relation."""

    endpoint_kinds: tuple[RelationEndpointKind, ...]
    tiers: tuple[QualifiedName, ...] | None = None
    minimum: int = 1
    maximum: int | None = None
    allow_empty: bool = False

    def __post_init__(self) -> None:
        """Canonicalize allowed sets and refuse incoherent arity bounds."""
        object.__setattr__(self, "endpoint_kinds", tuple(sorted(self.endpoint_kinds)))
        if self.tiers is not None:
            object.__setattr__(self, "tiers", tuple(sorted(self.tiers)))
        if not self.endpoint_kinds:
            raise GraphValidationError("relation side endpoint kinds must not be empty")
        if len(set(self.endpoint_kinds)) != len(self.endpoint_kinds):
            raise GraphValidationError("relation side endpoint kinds must be unique")
        if self.tiers is not None and len(set(self.tiers)) != len(self.tiers):
            raise GraphValidationError("relation side tiers must be unique")
        _require_integral_bound(self.minimum, "relation side minimum")
        if self.maximum is not None:
            _require_integral_bound(self.maximum, "relation side maximum")
        if self.maximum is not None and self.maximum < self.minimum:
            raise GraphValidationError(
                "relation side maximum must not be less than minimum"
            )
        _require_boolean(self.allow_empty, "relation side allow-empty promise")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the side contract without inventing order for its allowed sets."""
        return {
            "endpoint_kinds": [kind.value for kind in self.endpoint_kinds],
            "tiers": (
                None if self.tiers is None else [tier.to_data() for tier in self.tiers]
            ),
            "minimum": self.minimum,
            "maximum": -1 if self.maximum is None else self.maximum,
            "allow_empty": self.allow_empty,
        }


@dataclass(frozen=True, slots=True)
class PolyadicRelationDeclaration:
    """Declare ordered endpoint sequences and general incidence constraints.

    ``unique_sources`` makes each source occur in at most one instance.
    ``distinct_targets`` forbids repeated candidates within an instance.
    ``targets_subset_of`` requires each instance's targets to be members of the
    named relation's targets for the same source.  These are the structural
    contracts commonly called containment, choice, and selection membership;
    their domain names do not belong in the kernel.

    Empty sources or targets are admitted only by that side's ``allow_empty``.
    An empty side contributes no edges to acyclicity, no parent assignments to
    ``single_parent``, and no source keys to source uniqueness or subset checks.
    Its arity bounds are deliberately bypassed: emptiness is an explicit case,
    not an accidental consequence of a zero minimum.
    """

    name: QualifiedName
    sources: RelationSideDeclaration
    targets: RelationSideDeclaration
    unique_sources: bool = False
    distinct_targets: bool = False
    single_parent: bool = False
    acyclic: bool = False
    targets_subset_of: QualifiedName | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and require actual JSON-boolean promises."""
        _canonicalize_attributes(self)
        for value, subject in (
            (self.unique_sources, "unique-sources promise"),
            (self.distinct_targets, "distinct-targets promise"),
            (self.single_parent, "single-parent promise"),
            (self.acyclic, "acyclic promise"),
        ):
            _require_boolean(value, subject)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "kind": "polyadic",
            "name": self.name.to_data(),
            "sources": self.sources.to_data(),
            "targets": self.targets.to_data(),
            "unique_sources": self.unique_sources,
            "distinct_targets": self.distinct_targets,
            "single_parent": self.single_parent,
            "acyclic": self.acyclic,
            "targets_subset_of": []
            if self.targets_subset_of is None
            else [self.targets_subset_of.to_data()],
            "attributes": _attributes_data(self.attributes),
        }


type RelationDeclaration = (
    SimpleRelationDeclaration
    | BipartiteRelationDeclaration
    | PolyadicRelationDeclaration
)


@dataclass(frozen=True, slots=True)
class Item:
    """Represent a tier member with attributes and a durable identifier seam."""

    durable_id: str | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and refuse a carried empty durable id."""
        _canonicalize_attributes(self)
        if self.durable_id is not None:
            _require_name(self.durable_id, "item durable id")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the item as JSON-serializable data."""
        return {
            "durable_id": self.durable_id,
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class Tier:
    """Pair a declaration with immutable ordered members and tier attributes."""

    declaration: TierDeclaration
    items: tuple[Item, ...] = ()
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize tier attributes while retaining item order."""
        _canonicalize_attributes(self)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the tier as JSON-serializable data."""
        return {
            "declaration": self.declaration.to_data(),
            "items": [item.to_data() for item in self.items],
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class ItemRef:
    """Address an item by its current structural coordinate."""

    tier: QualifiedName
    index: int

    def __post_init__(self) -> None:
        """Require context-free integral identity before use or serialization."""
        _require_integral_index(self.index, "item reference", self.to_data())

    def to_data(self) -> dict[str, JsonValue]:
        """Return the reference as JSON-serializable data."""
        return {"tier": self.tier.to_data(), "index": self.index}

    def __str__(self) -> str:
        """Return a compact coordinate spelling for diagnostics."""
        return f"{self.tier}[{self.index}]"


@dataclass(frozen=True, slots=True)
class BoundaryRef:
    """Address a boundary owned by a tier, including both outer boundaries."""

    tier: QualifiedName
    index: int

    def __post_init__(self) -> None:
        """Require context-free integral identity before use or serialization."""
        _require_integral_index(self.index, "boundary", self.to_data())

    def to_data(self) -> dict[str, JsonValue]:
        """Return the boundary reference as JSON-serializable data."""
        return {"tier": self.tier.to_data(), "index": self.index}

    def __str__(self) -> str:
        """Return a compact coordinate spelling for diagnostics."""
        return f"{self.tier}[{self.index}]"


@dataclass(frozen=True, slots=True)
class Displacement:
    """Report where every position of one graph stands in another.

    The four maps are total over their source index spaces: an old position is
    either mapped or departed.  In particular, stationary positions map to
    themselves rather than being omitted.

    Construction refuses a coordinate that is both mapped and departed, which is
    the half of that claim a value can decide.  The other half cannot be checked
    here: a displacement does not carry the graph it is about, so the source
    space is whatever the maps and departed sets name between them, and a
    coordinate omitted from both is not detectable.  An accumulated displacement
    is total against a real graph because the operation that built it saw one;
    a hand-built one is total by definition rather than by check.
    """

    items: Mapping[ItemRef, ItemRef]
    boundaries: Mapping[BoundaryRef, BoundaryRef]
    relations: Mapping[int, int]
    polyadic_relations: Mapping[int, int]
    departed_items: frozenset[ItemRef]
    departed_boundaries: frozenset[BoundaryRef]
    departed_relations: frozenset[int]
    departed_polyadic_relations: frozenset[int]

    def __post_init__(self) -> None:
        """Detach the maps and require exclusive source-space partitions."""
        for name in ("items", "boundaries", "relations", "polyadic_relations"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        for mapped_name, departed_name in (
            ("items", "departed_items"),
            ("boundaries", "departed_boundaries"),
            ("relations", "departed_relations"),
            ("polyadic_relations", "departed_polyadic_relations"),
        ):
            overlap = set(getattr(self, mapped_name)).intersection(
                getattr(self, departed_name)
            )
            if overlap:
                position = min(overlap)
                raise GraphValidationError(
                    f"displacement {mapped_name} position {str(position)!r} is both "
                    "mapped and departed; every source position is exactly one"
                )

    def then(self, later: Displacement) -> Displacement:
        """Compose two displacements into the one the pair of edits performed."""
        items, departed_items = _compose_displacement_space(
            self.items, self.departed_items, later.items, later.departed_items
        )
        boundaries, departed_boundaries = _compose_displacement_space(
            self.boundaries,
            self.departed_boundaries,
            later.boundaries,
            later.departed_boundaries,
        )
        relations, departed_relations = _compose_displacement_space(
            self.relations,
            self.departed_relations,
            later.relations,
            later.departed_relations,
        )
        polyadic_relations, departed_polyadic_relations = _compose_displacement_space(
            self.polyadic_relations,
            self.departed_polyadic_relations,
            later.polyadic_relations,
            later.departed_polyadic_relations,
        )
        return Displacement(
            items,
            boundaries,
            relations,
            polyadic_relations,
            departed_items,
            departed_boundaries,
            departed_relations,
            departed_polyadic_relations,
        )

    @classmethod
    def stationary(cls, graph: Graph) -> Displacement:
        """Return the displacement of a graph onto itself."""
        items = {
            ItemRef(tier.declaration.name, index): ItemRef(tier.declaration.name, index)
            for tier in graph.tiers
            for index in range(len(tier.items))
        }
        boundaries = {
            BoundaryRef(tier.declaration.name, index): BoundaryRef(
                tier.declaration.name, index
            )
            for tier in graph.tiers
            for index in range(len(tier.items) + 1)
        }
        return cls(
            items,
            boundaries,
            {index: index for index in range(len(graph.relations))},
            {index: index for index in range(len(graph.polyadic_relations))},
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )


@dataclass(frozen=True, slots=True)
class DurableItemRef:
    """Address an item by a durable identifier without a coordinate fallback."""

    durable_id: str

    def __post_init__(self) -> None:
        """Require the identifier needed for durable resolution."""
        _require_name(self.durable_id, "durable item reference")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the durable reference as JSON-serializable data."""
        return {"durable_id": self.durable_id}


@dataclass(frozen=True, slots=True)
class DurableBoundaryRef:
    """Address a boundary whose identity is its anchor and chosen side.

    Boundary identity is anchor-relative: an interior boundary's identity is,
    for example, "before item X", not an identity attached to an adjacency.
    A boundary therefore follows its anchor when it moves; moving a block
    carries its internal boundaries.  Under reordering identities follow their
    anchors and no new adjacency inherits an identity.  Inserting exactly at
    ``before(x)`` leaves that boundary before ``x``.

    Distinct anchors may resolve to the same boundary in the current graph and
    diverge after an edit.  In particular, ``after(a)`` and ``before(b)`` keep
    different intentions even when ``a`` and ``b`` are adjacent.  Likewise,
    ``before(tier)`` and ``after(tier)`` are distinct first-edge and last-edge
    anchors that coincide only while the tier is empty.

    Removing an anchor is refused rather than reinterpreted.  Removal destroys
    the anchor, a boundary whose anchor is gone has no identity left to keep,
    and the kernel will not choose a replacement anchor on a caller's behalf.
    An edit that would remove such an item is therefore refused, immediately by
    a frozen graph's operation and at ``GraphEditor.freeze()`` by the editor's,
    and a caller who means to keep the boundary anchors it elsewhere first.
    """

    anchor: DurableItemRef | QualifiedName
    side: BoundarySide

    def to_data(self) -> dict[str, JsonValue]:
        """Return the tagged anchor and side as JSON-serializable data."""
        if isinstance(self.anchor, DurableItemRef):
            anchor: dict[str, JsonValue] = {
                "kind": "item",
                "durable_id": self.anchor.durable_id,
            }
        else:
            anchor = {"kind": "tier", "tier": self.anchor.to_data()}
        return {"anchor": anchor, "side": self.side.value}

    def __str__(self) -> str:
        """Return a compact anchored-boundary spelling for diagnostics."""
        anchor = (
            str(self.anchor)
            if isinstance(self.anchor, QualifiedName)
            else f"item {self.anchor.durable_id!r}"
        )
        return f"{self.side.value} {anchor}"


type RelationEndpointRef = ItemRef | DurableItemRef | DurableBoundaryRef


def _endpoint_data(reference: RelationEndpointRef) -> dict[str, JsonValue]:
    """Return an endpoint with the tag that disjoins durable item references."""
    if isinstance(reference, DurableItemRef):
        return {"kind": "durable-item", "durable_id": reference.durable_id}
    return reference.to_data()


@dataclass(frozen=True, slots=True)
class Boundary:
    """Hold values for one addressable boundary while empty boundaries stay derived."""

    reference: BoundaryRef | DurableBoundaryRef
    attributes: tuple[AttributeValue, ...]

    def __post_init__(self) -> None:
        """Canonicalize the values attached to this boundary."""
        _canonicalize_attributes(self)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the boundary and its values as JSON-serializable data."""
        return {
            "reference": self.reference.to_data(),
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class RelationInstance:
    """Link item or anchored-boundary endpoints through a declared relation."""

    declaration: QualifiedName
    left: RelationEndpointRef
    right: RelationEndpointRef
    durable_id: str | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and require a usable carried durable id."""
        _canonicalize_attributes(self)
        if self.durable_id is not None:
            _require_name(self.durable_id, "relation instance durable id")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the instance as JSON-serializable data."""
        return {
            "declaration": self.declaration.to_data(),
            "left": _endpoint_data(self.left),
            "right": _endpoint_data(self.right),
            "durable_id": self.durable_id,
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class PolyadicRelationInstance:
    """Link two declared, ordered endpoint sequences."""

    declaration: QualifiedName
    sources: tuple[RelationEndpointRef, ...]
    targets: tuple[RelationEndpointRef, ...]
    durable_id: str | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and require a usable carried durable id."""
        _canonicalize_attributes(self)
        if self.durable_id is not None:
            _require_name(self.durable_id, "relation instance durable id")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the ordered sides as JSON-serializable arrays."""
        return {
            "declaration": self.declaration.to_data(),
            "sources": [_endpoint_data(endpoint) for endpoint in self.sources],
            "targets": [_endpoint_data(endpoint) for endpoint in self.targets],
            "durable_id": self.durable_id,
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class OrphanedSubject:
    """Name where a fact stood when an edit left its subject no image.

    The old coordinate and its carrier are retained, never re-anchored. Orphans
    are unreachable from reads and accumulate until a caller constructs a layer
    without them; ``flatten`` refuses rather than hiding that cost in the base.
    """

    carrier: SealedCarrier
    was: ItemRef | BoundaryRef | int


type LayerSubject = (
    ItemRef
    | DurableItemRef
    | BoundaryRef
    | DurableBoundaryRef
    | TierRef
    | RelationDeclarationRef
    | RelationInstanceRef
    | DurableRelationRef
    | PolyadicInstanceRef
    | DurablePolyadicRef
    | DocumentRef
    | OrphanedSubject
)


@dataclass(frozen=True, slots=True)
class LayerFact:
    """State one named typed value at one subject of the base."""

    subject: LayerSubject
    value: AttributeValue


@dataclass(frozen=True, slots=True)
class Layer:
    """Hold one source's attribute facts and nothing structural."""

    name: LayerName
    facts: tuple[LayerFact, ...]

    def to_data(self) -> dict[str, JsonValue]:
        """Return the layer and its tagged facts as JSON-serializable data."""
        return {
            "name": self.name.to_data(),
            "facts": [
                {
                    "subject": _layer_subject_data(fact.subject),
                    "value": fact.value.to_data(),
                }
                for fact in self.facts
            ],
        }


@dataclass(frozen=True, slots=True)
class Delivery:
    """Select layers in lowest-to-highest precedence order; read is explicit."""

    layers: tuple[LayerName, ...]
    read: LayerRead


@dataclass(frozen=True, slots=True)
class Consensus:
    """Report every delivered reading and whether their canonical values agree."""

    subject: LayerSubject
    name: QualifiedName
    readings: tuple[tuple[LayerName, AttributeValue], ...]
    agreed: bool


class _GraphIndexes(NamedTuple):
    """Bundle the four derived indexes shared by graph validation phases."""

    tiers: dict[QualifiedName, Tier]
    types: dict[QualifiedName, QualifiedName]
    boundaries: dict[BoundaryRef, Boundary]
    items: dict[str, ItemRef]


@dataclass(frozen=True, slots=True)
class Graph:
    """Hold a validated immutable graph and derive order and empty boundaries.

    Collections keyed by names or references are canonicalized because supply
    order has no graph meaning: namespaces, relation and attribute declarations,
    every attribute-value collection, seals, layers and the facts within each
    layer, sparse boundary values, and relation-side allowed kinds and tiers.
    Tiers, tier items, relation instances, and polyadic endpoint sequences
    remain ordered because their sequence carries graph meaning.
    """

    namespaces: tuple[NamespaceDeclaration, ...]
    tiers: tuple[Tier, ...]
    relation_declarations: tuple[RelationDeclaration, ...]
    relations: tuple[RelationInstance, ...] = ()
    attribute_declarations: tuple[AttributeDeclaration, ...] = ()
    boundary_values: tuple[Boundary, ...] = ()
    attributes: tuple[AttributeValue, ...] = ()
    polyadic_relations: tuple[PolyadicRelationInstance, ...] = ()
    seals: tuple[Seal, ...] = ()
    layers: tuple[Layer, ...] = ()
    _tiers_by_name: dict[QualifiedName, Tier] = field(
        init=False, repr=False, compare=False
    )
    _types_by_tier: dict[QualifiedName, QualifiedName] = field(
        init=False, repr=False, compare=False
    )
    _boundaries_by_ref: dict[BoundaryRef, Boundary] = field(
        init=False, repr=False, compare=False
    )
    _items_by_id: dict[str, ItemRef] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Canonicalize keyed collections and validate the complete graph."""
        self._canonicalize_collections()
        namespaces = _unique_by_name(
            ((binding.prefix, binding) for binding in self.namespaces),
            "namespace prefix",
        )
        duplicate_uri = _duplicate([binding.namespace for binding in self.namespaces])
        if duplicate_uri is not None:
            raise GraphValidationError(
                f"duplicate namespace URI {duplicate_uri!r}; each URI needs one prefix"
            )
        declared_namespaces = {binding.namespace for binding in namespaces.values()}
        tiers_by_name = _unique_by_name(
            ((tier.declaration.name, tier) for tier in self.tiers), "tier"
        )
        seals = _unique_by_name(
            ((seal.carrier, seal) for seal in self.seals), "seal carrier"
        )
        for carrier, seal in seals.items():
            count = _carrier_count(self, carrier, tiers_by_name)
            if seal.sealed < 0:
                raise GraphValidationError(
                    f"seal on {str(carrier)!r} must not be negative"
                )
            if seal.sealed > count:
                raise GraphValidationError(
                    f"seal on {str(carrier)!r} at {seal.sealed} names more members "
                    f"than the {_carrier_kind(carrier)} holds, which is {count}; a "
                    "seal covers members that exist"
                )
        declarations = _unique_by_name(
            (
                (declaration.name, declaration)
                for declaration in self.relation_declarations
            ),
            "relation declaration",
        )
        attributes = _unique_by_name(
            (
                (declaration.name, declaration)
                for declaration in self.attribute_declarations
            ),
            "attribute declaration",
        )
        layer_names = _unique_by_name(
            ((layer.name, layer) for layer in self.layers), "layer"
        )
        qualified_names = [
            *(tier.declaration.name for tier in self.tiers),
            *(declaration.name for declaration in self.relation_declarations),
            *(
                declaration.item_type
                for declaration in self.relation_declarations
                if isinstance(declaration, SimpleRelationDeclaration)
            ),
            *(
                endpoint_type
                for declaration in self.relation_declarations
                if isinstance(declaration, BipartiteRelationDeclaration)
                for endpoint_type in (declaration.left_type, declaration.right_type)
            ),
            *(
                tier
                for declaration in self.relation_declarations
                if isinstance(declaration, PolyadicRelationDeclaration)
                for side in (declaration.sources, declaration.targets)
                for tier in (() if side.tiers is None else side.tiers)
            ),
            *(
                declaration.targets_subset_of
                for declaration in self.relation_declarations
                if isinstance(declaration, PolyadicRelationDeclaration)
                and declaration.targets_subset_of is not None
            ),
            *(declaration.name for declaration in self.attribute_declarations),
            # An orphan is the one layer subject nothing resolves, so its
            # coordinate is the one qualified name in this graph that no other
            # check reaches: `_validate_layer_fact` sends every live subject to
            # `_resolve_layer_subject`, which refuses a name the graph does not
            # declare, and skips the orphan exactly because there is nothing
            # left to resolve it against. Its spelling is still a qualified
            # name, and the contract above is a contract about every one of
            # them, so leaving it out of this list was an omission rather than
            # an exemption: the graph validated and `dumps` then reached the
            # encoder's bare `prefixes[namespace]` with no prefix to find,
            # raising `KeyError` where `to_data` says the one condition it
            # answers is the encoding one and that it raises `Refusal`.
            *(
                name
                for layer in self.layers
                for fact in layer.facts
                if isinstance(fact.subject, OrphanedSubject)
                for name in _orphan_names(fact.subject)
            ),
        ]
        for name in qualified_names:
            if name.namespace not in declared_namespaces:
                raise GraphValidationError(
                    f"qualified name {str(name)!r} uses undeclared namespace {name.namespace!r}"
                )
        simple = [
            declaration
            for declaration in self.relation_declarations
            if isinstance(declaration, SimpleRelationDeclaration)
        ]
        types_by_tier = _unique_simple_types(simple, tiers_by_name)
        items_by_id = {
            item.durable_id: ItemRef(tier.declaration.name, index)
            for tier in self.tiers
            for index, item in enumerate(tier.items)
            if item.durable_id is not None
        }
        durable_ids = [
            (item.durable_id, f"item at tier {tier_index}, index {item_index}")
            for tier_index, tier in enumerate(self.tiers)
            for item_index, item in enumerate(tier.items)
            if item.durable_id is not None
        ]
        durable_ids.extend(
            (relation.durable_id, f"relation instance {index}")
            for index, relation in enumerate(self.relations)
            if relation.durable_id is not None
        )
        durable_ids.extend(
            (relation.durable_id, f"polyadic relation instance {index}")
            for index, relation in enumerate(self.polyadic_relations)
            if relation.durable_id is not None
        )
        self._validate_attribute_values(attributes)
        bipartite, polyadic = self._validate_relation_instances(
            declarations, attributes, tiers_by_name, types_by_tier, items_by_id
        )
        boundaries_by_ref = self._validate_boundaries(
            attributes, tiers_by_name, items_by_id
        )
        indexes = _GraphIndexes(
            tiers_by_name, types_by_tier, boundaries_by_ref, items_by_id
        )
        self._validate_invariants_and_install(durable_ids, bipartite, polyadic, indexes)
        self._validate_layers(layer_names, attributes)

    def _canonicalize_collections(self) -> None:
        """Canonicalize every keyed collection before validation."""
        _canonicalize_attributes(self)
        object.__setattr__(
            self,
            "namespaces",
            tuple(sorted(self.namespaces, key=lambda item: item.namespace)),
        )
        object.__setattr__(
            self,
            "relation_declarations",
            tuple(sorted(self.relation_declarations, key=lambda item: item.name)),
        )
        object.__setattr__(
            self,
            "attribute_declarations",
            tuple(sorted(self.attribute_declarations, key=lambda item: item.name)),
        )
        object.__setattr__(self, "seals", tuple(sorted(self.seals)))
        object.__setattr__(
            self,
            "layers",
            tuple(
                sorted(
                    (
                        Layer(
                            layer.name, tuple(sorted(layer.facts, key=_layer_fact_key))
                        )
                        for layer in self.layers
                    ),
                    key=lambda layer: layer.name,
                )
            ),
        )

    def _validate_attribute_values(
        self, attributes: dict[QualifiedName, AttributeDeclaration]
    ) -> None:
        """Validate attribute values in every structural domain."""
        _validate_attributes(self.attributes, AttributeDomain.DOCUMENT, attributes)
        for tier in self.tiers:
            _validate_attributes(tier.attributes, AttributeDomain.TIER, attributes)
            for item in tier.items:
                _validate_attributes(item.attributes, AttributeDomain.ITEM, attributes)
        for declaration in self.relation_declarations:
            _validate_attributes(
                declaration.attributes,
                AttributeDomain.RELATION_DECLARATION,
                attributes,
            )

    def _validate_relation_instances(
        self,
        declarations: dict[QualifiedName, RelationDeclaration],
        attributes: dict[QualifiedName, AttributeDeclaration],
        tiers_by_name: dict[QualifiedName, Tier],
        types_by_tier: dict[QualifiedName, QualifiedName],
        items_by_id: dict[str, ItemRef],
    ) -> tuple[
        dict[QualifiedName, BipartiteRelationDeclaration],
        dict[QualifiedName, PolyadicRelationDeclaration],
    ]:
        """Validate bipartite and polyadic relation instances."""
        bipartite = {
            name: declaration
            for name, declaration in declarations.items()
            if isinstance(declaration, BipartiteRelationDeclaration)
        }
        polyadic = {
            name: declaration
            for name, declaration in declarations.items()
            if isinstance(declaration, PolyadicRelationDeclaration)
        }
        for index, relation in enumerate(self.relations):
            _validate_attributes(
                relation.attributes, AttributeDomain.RELATION_INSTANCE, attributes
            )
            bipartite_declaration = bipartite.get(relation.declaration)
            if bipartite_declaration is None:
                raise GraphValidationError(
                    f"relation instance {index} names {str(relation.declaration)!r}; "
                    "a bipartite relation declaration is required"
                )
            _validate_endpoint(
                index,
                "left",
                relation.left,
                bipartite_declaration.left_type,
                bipartite_declaration.left_endpoint,
                tiers_by_name,
                types_by_tier,
                items_by_id,
            )
            _validate_endpoint(
                index,
                "right",
                relation.right,
                bipartite_declaration.right_type,
                bipartite_declaration.right_endpoint,
                tiers_by_name,
                types_by_tier,
                items_by_id,
            )
        for index, polyadic_relation in enumerate(self.polyadic_relations):
            _validate_attributes(
                polyadic_relation.attributes,
                AttributeDomain.RELATION_INSTANCE,
                attributes,
            )
            polyadic_declaration = polyadic.get(polyadic_relation.declaration)
            if polyadic_declaration is None:
                raise GraphValidationError(
                    f"polyadic relation instance {index} names {str(polyadic_relation.declaration)!r}; "
                    "a polyadic relation declaration is required"
                )
            _validate_polyadic_instance(
                index,
                polyadic_relation,
                polyadic_declaration,
                tiers_by_name,
                items_by_id,
            )
        return bipartite, polyadic

    def _validate_boundaries(
        self,
        attributes: dict[QualifiedName, AttributeDeclaration],
        tiers_by_name: dict[QualifiedName, Tier],
        items_by_id: dict[str, ItemRef],
    ) -> dict[BoundaryRef, Boundary]:
        """Validate, resolve, and canonicalize sparse boundary values."""
        valued_boundaries: list[tuple[BoundaryRef, Boundary]] = []
        for boundary in self.boundary_values:
            coordinate = _resolve_boundary_reference(
                boundary.reference,
                tiers_by_name,
                items_by_id,
                GraphValidationError,
            )
            valued_boundaries.append((coordinate, boundary))
            if not boundary.attributes:
                raise GraphValidationError(
                    f"boundary {str(boundary.reference)!r} has no attribute values; "
                    "empty boundaries are derived"
                )
            _validate_attributes(
                boundary.attributes, AttributeDomain.BOUNDARY, attributes
            )
        boundaries_by_ref = _unique_by_name(valued_boundaries, "boundary value")
        object.__setattr__(
            self,
            "boundary_values",
            tuple(
                boundary
                for _, boundary in sorted(
                    valued_boundaries,
                    key=lambda entry: (entry[0].tier, entry[0].index),
                )
            ),
        )
        return boundaries_by_ref

    def _validate_invariants_and_install(
        self,
        durable_ids: list[tuple[str, str]],
        bipartite: dict[QualifiedName, BipartiteRelationDeclaration],
        polyadic: dict[QualifiedName, PolyadicRelationDeclaration],
        indexes: _GraphIndexes,
    ) -> None:
        """Validate cross-instance invariants and install derived indexes."""
        _require_unique_durable_ids(durable_ids)
        _validate_relation_invariants(
            self.relations, bipartite, indexes.tiers, indexes.items
        )
        _validate_polyadic_invariants(
            self.polyadic_relations, polyadic, indexes.tiers, indexes.items
        )
        object.__setattr__(self, "_tiers_by_name", indexes.tiers)
        object.__setattr__(self, "_types_by_tier", indexes.types)
        object.__setattr__(self, "_boundaries_by_ref", indexes.boundaries)
        object.__setattr__(
            self,
            "_items_by_id",
            indexes.items,
        )

    def _validate_layers(
        self,
        layer_names: dict[LayerName, Layer],
        attributes: dict[QualifiedName, AttributeDeclaration],
    ) -> None:
        """Validate layer ownership, uniqueness, and subjects."""
        for layer in layer_names.values():
            seen: set[tuple[LayerSubject, QualifiedName]] = set()
            for fact in layer.facts:
                if fact.value.name.namespace != layer.name.vocabulary:
                    raise GraphValidationError(
                        f"layer {layer.name.vocabulary!r}/{layer.name.source!r} states "
                        f"{str(fact.value.name)!r}; a layer writes in its named vocabulary"
                    )
                key = (fact.subject, fact.value.name)
                if key in seen:
                    raise GraphValidationError(
                        f"layer {layer.name.vocabulary!r}/{layer.name.source!r} states "
                        f"{str(fact.value.name)!r} twice at {str(fact.subject)!r}; one "
                        "layer states one value at one subject, and a second producer "
                        "states its own in its own layer"
                    )
                seen.add(key)
                self._validate_layer_fact(layer, fact, attributes)

    def _validate_layer_fact(
        self,
        layer: Layer,
        fact: LayerFact,
        declarations: Mapping[QualifiedName, AttributeDeclaration],
    ) -> None:
        """Require a live subject and the declaration domain it promises."""
        declaration = declarations.get(fact.value.name)
        if declaration is None:
            raise GraphValidationError(
                f"attribute {str(fact.value.name)!r} is undeclared"
            )
        if declaration.value_type is not fact.value.value_type:
            raise GraphValidationError(
                f"attribute {str(fact.value.name)!r} requires {declaration.value_type.value}, "
                f"not {fact.value.value_type.value}"
            )
        expected = _layer_subject_domain(fact.subject)
        if declaration.domain is not expected:
            raise GraphValidationError(
                f"layer {layer.name.vocabulary!r}/{layer.name.source!r} states "
                f"{str(fact.value.name)!r} at {_domain_article(expected)}, but that name "
                f"is declared for the {declaration.domain.value} domain; a value is "
                "carried where its declaration says it is carried"
            )
        if isinstance(fact.subject, OrphanedSubject):
            _validate_orphaned_subject(layer, fact.subject)
        else:
            _resolve_layer_subject(self, fact.subject)

    def layer_values(
        self, subject: LayerSubject, name: QualifiedName, delivery: Delivery
    ) -> tuple[AttributeValue, ...]:
        """Return what the explicit delivery reads at this live subject and name."""
        readings = self._layer_readings(subject, name, delivery)
        if delivery.read is LayerRead.FIRST:
            return tuple(value for _, value in readings[:1])
        if delivery.read is LayerRead.LAST:
            return tuple(value for _, value in readings[-1:])
        return tuple(value for _, value in readings)

    def consensus(
        self, subject: LayerSubject, name: QualifiedName, delivery: Delivery
    ) -> Consensus:
        """Report every delivered statement and whether canonical values agree."""
        readings = self._layer_readings(subject, name, delivery)
        return Consensus(
            subject,
            name,
            readings,
            bool(readings) and len({value for _, value in readings}) == 1,
        )

    def disagreements(self, delivery: Delivery) -> tuple[Consensus, ...]:
        """Return only delivered subject/name rows carrying unequal readings."""
        keys = {
            (fact.subject, fact.value.name)
            for layer in self._delivered(delivery)
            for fact in layer.facts
            if not isinstance(fact.subject, OrphanedSubject)
        }
        return tuple(
            report
            for subject, name in sorted(keys, key=_layer_key)
            if not (report := self.consensus(subject, name, delivery)).agreed
        )

    def flatten(self, delivery: Delivery) -> Graph:
        """Write selected readings into a layerless base, refusing ambiguity/orphans."""
        delivered = self._delivered(delivery)
        for layer in delivered:
            for fact in layer.facts:
                if isinstance(fact.subject, OrphanedSubject):
                    raise GraphValidationError(
                        f"delivery cannot be flattened: layer {layer.name.vocabulary!r}/"
                        f"{layer.name.source!r} holds {str(fact.value.name)!r} orphaned "
                        f"from {str(fact.subject.was)!r}, and an orphaned fact has no "
                        "subject in the base to be written to. Re-anchor it, or leave "
                        "that layer out of the delivery."
                    )
        keys = {
            (fact.subject, fact.value.name)
            for layer in delivered
            for fact in layer.facts
        }
        editor = self.edit()
        editor._layers = []
        for subject, name in keys:
            values = self.layer_values(subject, name, delivery)
            if len(values) > 1:
                readings = self._layer_readings(subject, name, delivery)
                first, second = readings[:2]
                raise GraphValidationError(
                    f"delivery cannot be flattened: layers {first[0].vocabulary!r}/"
                    f"{first[0].source!r} and {second[0].vocabulary!r}/"
                    f"{second[0].source!r} both state {str(name)!r} at "
                    f"{str(subject)!r}, and one subject carries at most one value "
                    "under one name. Flatten under FIRST or LAST to choose one, or "
                    "keep the layers and read them."
                )
            if values:  # pragma: no branch - keys come only from delivered facts
                editor.set_attribute(_layer_edit_target(self, subject), values[0])
        return editor.freeze()

    def promotion(self, tier: QualifiedName) -> bool:
        """Report whether every item on a tier carries durable identity."""
        member = self._tiers_by_name.get(tier)
        if member is None:
            raise ValueError(f"promotion names undeclared tier {str(tier)!r}")
        return all(item.durable_id is not None for item in member.items)

    def _delivered(self, delivery: Delivery) -> tuple[Layer, ...]:
        if not delivery.layers:
            raise GraphValidationError(
                "delivery names no layers; a read with nothing to read from has no answer to give, and an empty result would look like agreement"
            )
        if len(set(delivery.layers)) != len(delivery.layers):
            duplicate = next(
                name for name in delivery.layers if delivery.layers.count(name) > 1
            )
            raise GraphValidationError(
                f"delivery names layer {duplicate.vocabulary!r}/{duplicate.source!r} twice; a delivery is a precedence order and a layer has one place in it"
            )
        held = {layer.name: layer for layer in self.layers}
        missing = next((name for name in delivery.layers if name not in held), None)
        if missing is not None:
            raise GraphValidationError(
                f"delivery names layer {missing.vocabulary!r}/{missing.source!r}, which this graph does not hold"
            )
        return tuple(held[name] for name in delivery.layers)

    def _layer_readings(
        self, subject: LayerSubject, name: QualifiedName, delivery: Delivery
    ) -> tuple[tuple[LayerName, AttributeValue], ...]:
        if isinstance(subject, OrphanedSubject):
            return ()
        return tuple(
            (layer.name, fact.value)
            for layer in self._delivered(delivery)
            for fact in layer.facts
            if fact.subject == subject and fact.value.name == name
        )

    def boundaries(self, tier: QualifiedName) -> tuple[Boundary, ...]:
        """Return every addressable boundary with sparse values joined on demand."""
        member_tier = self._tiers_by_name.get(tier)
        if member_tier is None:
            raise ValueError(f"boundary tier {str(tier)!r} is not declared")
        boundaries = []
        for index in range(len(member_tier.items) + 1):
            reference = BoundaryRef(tier, index)
            stored = self._boundaries_by_ref.get(reference)
            boundaries.append(
                Boundary(reference, stored.attributes)
                if stored is not None
                else Boundary(reference, ())
            )
        return tuple(boundaries)

    def canonical_items(self) -> tuple[ItemRef, ...]:
        """Compute tier-major canonical order without storing it."""
        return tuple(
            ItemRef(tier.declaration.name, index)
            for tier in self.tiers
            for index in range(len(tier.items))
        )

    def item_type(self, reference: ItemRef) -> QualifiedName:
        """Return the type supplied by simple membership or refuse an untyped tier."""
        _validate_reference(
            reference, "item reference", self._tiers_by_name, ValueError
        )
        item_type = self._types_by_tier.get(reference.tier)
        if item_type is None:
            raise ValueError(
                f"item reference tier {str(reference.tier)!r} has no simple relation and is untyped"
            )
        return item_type

    def resolve_item(self, reference: ItemRef | DurableItemRef) -> ItemRef:
        """Resolve either identity level to the item's current coordinate."""
        if isinstance(reference, ItemRef):
            _validate_reference(
                reference, "item reference", self._tiers_by_name, ValueError
            )
            return reference
        if not isinstance(reference, DurableItemRef):
            raise TypeError(
                "item resolution expected ItemRef or DurableItemRef; "
                f"got {type(reference).__name__}"
            )
        coordinate = self._items_by_id.get(reference.durable_id)
        if coordinate is None:
            raise ValueError(f"unknown durable item id {reference.durable_id!r}")
        return coordinate

    def resolve_boundary(
        self, reference: BoundaryRef | DurableBoundaryRef
    ) -> BoundaryRef:
        """Resolve either identity level to the boundary's current coordinate."""
        if isinstance(reference, BoundaryRef):
            _validate_boundary(reference, self._tiers_by_name, ValueError)
            return reference
        if not isinstance(reference, DurableBoundaryRef):
            raise TypeError(
                "boundary resolution expected BoundaryRef or DurableBoundaryRef; "
                f"got {type(reference).__name__}"
            )
        return _resolve_boundary_reference(
            reference, self._tiers_by_name, self._items_by_id, ValueError
        )

    def promote_item(
        self, reference: ItemRef, durable_id: str
    ) -> tuple[Graph, DurableItemRef]:
        """Return a graph carrying the caller's semantic id for one item.

        The durable id is as-built content, so adding it changes canonical bytes
        and the construction fingerprint.  Repeating the same id is idempotent;
        a different id is refused and never replaces the established identity.
        """
        _validate_reference(
            reference, "item reference", self._tiers_by_name, ValueError
        )
        tier = self._tiers_by_name[reference.tier]
        item = tier.items[reference.index]
        if item.durable_id is not None:
            if item.durable_id != durable_id:
                raise ValueError(
                    f"item {str(reference)!r} already carries durable id "
                    f"{item.durable_id!r}; refused conflicting durable id "
                    f"{durable_id!r}"
                )
            return self, DurableItemRef(item.durable_id)
        items = list(tier.items)
        items[reference.index] = Item(durable_id, item.attributes)
        replacement = Tier(tier.declaration, tuple(items), tier.attributes)
        tiers = tuple(
            replacement if candidate is tier else candidate for candidate in self.tiers
        )
        return self._replace(tiers=tiers), DurableItemRef(durable_id)

    def promote_boundary(
        self, reference: BoundaryRef, durable_id: str
    ) -> tuple[Graph, DurableBoundaryRef]:
        """Return a graph whose boundary anchor has durable identity.

        Promoting an interior boundary promotes its anchor item.  That durable
        id is as-built content, so adding it changes canonical bytes and the
        construction fingerprint.  An anchor carrying a different id refuses
        the requested boundary identity rather than replacing its own.
        """
        _validate_boundary(reference, self._tiers_by_name, ValueError)
        tier = self._tiers_by_name[reference.tier]
        if reference.index == 0:
            promoted = self
            durable = DurableBoundaryRef(reference.tier, BoundarySide.BEFORE)
        elif reference.index == len(tier.items):
            promoted = self
            durable = DurableBoundaryRef(reference.tier, BoundarySide.AFTER)
        else:
            anchor_reference = ItemRef(reference.tier, reference.index)
            anchor_item = tier.items[reference.index]
            if (
                anchor_item.durable_id is not None
                and anchor_item.durable_id != durable_id
            ):
                raise ValueError(
                    f"boundary {str(reference)!r} is before an anchor carrying "
                    f"durable id {anchor_item.durable_id!r}; refused conflicting "
                    f"boundary durable id {durable_id!r}"
                )
            promoted, anchor = self.promote_item(anchor_reference, durable_id)
            durable = DurableBoundaryRef(anchor, BoundarySide.BEFORE)
        boundary = promoted._boundaries_by_ref.get(reference)
        if boundary is None:
            return promoted, durable
        if isinstance(boundary.reference, DurableBoundaryRef):
            return promoted, boundary.reference
        anchored = Boundary(durable, boundary.attributes)
        values = tuple(
            anchored if candidate is boundary else candidate
            for candidate in promoted.boundary_values
        )
        return promoted._replace(boundary_values=values), durable

    def _replace(
        self,
        *,
        tiers: tuple[Tier, ...] | None = None,
        boundary_values: tuple[Boundary, ...] | None = None,
    ) -> Graph:
        """Rebuild immutable graph content for a promotion operation."""
        return replace(
            self,
            tiers=self.tiers if tiers is None else tiers,
            boundary_values=(
                self.boundary_values if boundary_values is None else boundary_values
            ),
        )

    def to_data(self) -> dict[str, JsonValue]:
        """Return graph content in canonical declaration order as JSON data."""
        return {
            "namespaces": [binding.to_data() for binding in self.namespaces],
            "tiers": [tier.to_data() for tier in self.tiers],
            "relation_declarations": [
                declaration.to_data() for declaration in self.relation_declarations
            ],
            "relations": [
                relation.to_data()
                for relation in (*self.relations, *self.polyadic_relations)
            ],
            "attribute_declarations": [
                declaration.to_data() for declaration in self.attribute_declarations
            ],
            # The wire key retains its earlier spelling; only the field renamed.
            "position_values": [
                boundary.to_data() for boundary in self.boundary_values
            ],
            "attributes": _attributes_data(self.attributes),
            "seals": [seal.to_data() for seal in self.seals],
            "layers": [layer.to_data() for layer in self.layers],
        }

    def seal(self, carrier: SealedCarrier, sealed: int) -> Graph:
        """Return a graph sealing this much of one carrier, refusing a retreat."""
        current = next((item for item in self.seals if item.carrier == carrier), None)
        if current is not None and sealed < current.sealed:
            raise GraphValidationError(
                f"seal on {str(carrier)!r} stands at {current.sealed} and cannot be "
                f"set to {sealed}; sealing advances. Use unseal to say that what you "
                "were given is what you mean to change."
            )
        return self._with_seal(carrier, sealed)

    def unseal(self, carrier: SealedCarrier, sealed: int) -> Graph:
        """Return a graph whose seal on one carrier stands lower than it did."""
        current = next((item for item in self.seals if item.carrier == carrier), None)
        if current is None:
            raise GraphValidationError(
                f"cannot unseal {str(carrier)!r} to {sealed}: this graph carries no "
                "seal on that carrier"
            )
        if sealed >= current.sealed:
            raise GraphValidationError(
                f"cannot unseal {str(carrier)!r} from {current.sealed} to {sealed}: "
                "the requested seal is not lower"
            )
        return self._with_seal(carrier, sealed)

    def is_sealed(self, coordinate: ItemRef | BoundaryRef) -> bool:
        """Report whether this coordinate stands inside its carrier's seal."""
        return any(
            seal.carrier == coordinate.tier and coordinate.index < seal.sealed
            for seal in self.seals
        )

    def _with_seal(self, carrier: SealedCarrier, sealed: int) -> Graph:
        kept = tuple(item for item in self.seals if item.carrier != carrier)
        return replace(self, seals=(*kept, Seal(carrier, sealed)))

    def edit(self) -> GraphEditor:
        """Return a mutable editor holding a copy of this graph's content.

        The editor answers the same operations this graph answers, and answers
        them in place: one validation runs at ``freeze()`` instead of one per
        operation.  Whether an operation rewrites or mutates follows from the
        carrier the caller holds, never from an argument passed to it.
        """
        return GraphEditor(self)

    def declare(self, declaration: EditDeclaration) -> Graph:
        """Return a new graph carrying one more declaration."""
        return self.edit().declare(declaration).freeze()

    def set_attribute(self, target: EditTarget, value: AttributeValue) -> Graph:
        """Return a new graph whose target carries this value under its name."""
        return self.edit().set_attribute(target, value).freeze()

    def remove_attribute(self, target: EditTarget, name: QualifiedName) -> Graph:
        """Return a new graph whose target no longer carries this name."""
        return self.edit().remove_attribute(target, name).freeze()

    def insert_item(self, tier: QualifiedName, index: int, item: Item) -> Graph:
        """Return a new graph with one more item at this tier index."""
        return self.edit().insert_item(tier, index, item).freeze()

    def remove_item(self, reference: ItemRef | DurableItemRef) -> Graph:
        """Return a new graph without this item."""
        return self.edit().remove_item(reference).freeze()

    def move_item(self, reference: ItemRef | DurableItemRef, index: int) -> Graph:
        """Return a new graph with this item at another index of its own tier."""
        return self.edit().move_item(reference, index).freeze()

    def swap_items(
        self,
        first: ItemRef | DurableItemRef,
        second: ItemRef | DurableItemRef,
    ) -> Graph:
        """Return a new graph with two items of one tier exchanged."""
        return self.edit().swap_items(first, second).freeze()

    def add_relation(
        self, instance: RelationInstance | PolyadicRelationInstance
    ) -> Graph:
        """Return a new graph carrying one more relation instance."""
        return self.edit().add_relation(instance).freeze()

    def remove_relation(self, target: int | str) -> Graph:
        """Return a new graph without the relation instance this names."""
        return self.edit().remove_relation(target).freeze()


@dataclass(frozen=True, slots=True)
class SealDeclaration:
    """Bind the seals one graph carries to the graph that claims to honor them.

    The cone model is reserved until a whole-graph seal exists as one frozen base,
    a mergeable delta type exists, coordinate removal is expressible within a
    footprint or excluded from the mergeable set, and observed-read validation is
    decided.
    """

    name: str
    source: Graph
    result: Graph

    def __post_init__(self) -> None:
        """Require a rewrite name because refusals report it."""
        if not self.name:
            raise ValueError("rewrite name '' must not be empty")

    def breaches(self) -> tuple[SealBreach, ...]:
        """Return every sealed member the result disturbed, in carrier order."""
        breaches: list[SealBreach] = []
        result_seals = {seal.carrier: seal.sealed for seal in self.result.seals}
        for seal in self.source.seals:
            if result_seals.get(seal.carrier, -1) < seal.sealed:
                breaches.append(
                    SealBreach(seal.carrier, 0, "the result carries no matching seal")
                )
                continue
            before = _carrier_members(self.source, seal.carrier)
            after = _carrier_members(self.result, seal.carrier)
            for index in range(seal.sealed):
                identity = _member_identity(before[index])
                if index >= len(after) or (
                    identity is not None and identity != _member_identity(after[index])
                ):
                    breaches.append(
                        SealBreach(
                            seal.carrier,
                            index,
                            _seal_breach_detail(
                                seal.carrier, index, before[index], after[index]
                            ),
                        )
                    )
        return tuple(breaches)

    def check_seals(self) -> SealCertificate:
        """Demand that the result honor the source's seals, or refuse."""
        breaches = self.breaches()
        if breaches:
            breach = breaches[0]
            source_seal = next(
                seal for seal in self.source.seals if seal.carrier == breach.carrier
            )
            result_seal = next(
                (seal for seal in self.result.seals if seal.carrier == breach.carrier),
                None,
            )
            if result_seal is None or result_seal.sealed < source_seal.sealed:
                detail = (
                    "the result carries no seal on that carrier. A seal is not "
                    "withdrawn by omission; a graph that means to unseal says so."
                )
            else:
                detail = (
                    f"{breach.detail}. A seal is honored when every discriminable "
                    "member up to it stands at the same coordinate. The breach "
                    "reported is the first in the carrier's own order rather than "
                    "the only one or the worst one."
                )
            raise GraphValidationError(
                f"rewrite {self.name!r} does not honor the source's seal on "
                f"{str(breach.carrier)!r} at {source_seal.sealed}: {detail}"
            )
        return SealCertificate(
            len(self.source.seals),
            sum(
                _member_identity(member) is not None
                for seal in self.source.seals
                for member in _carrier_members(self.source, seal.carrier)[: seal.sealed]
            ),
        )


type EditTarget = (
    None
    | QualifiedName
    | ItemRef
    | DurableItemRef
    | BoundaryRef
    | DurableBoundaryRef
    | int
    | str
)

type EditDeclaration = (
    NamespaceDeclaration | TierDeclaration | AttributeDeclaration | RelationDeclaration
)


class GraphEditor:
    """Carry graph content in mutable form and validate it once at freeze.

    A frozen ``Graph`` answers this operation set by returning a new graph.
    This carrier answers the same operations by changing itself, so a caller
    chooses rewriting or mutation by choosing which carrier to hold.  Every
    operation returns this editor so operations chain, and nothing it returns
    is a graph until ``freeze()`` builds and validates one.

    Structural operations keep the graph's own references denoting what they
    denoted before the edit.  Item coordinates stored inside the graph are
    rewritten to follow their items, and durable identifiers resolve again at
    freeze.  A stored boundary value addressed by coordinate is rewritten when
    the edit leaves its boundary exactly one image, and refuses the edit when
    it does not: a bare coordinate has no anchor to follow, while a boundary
    promoted through ``Graph.promote_boundary`` does.

    An operation that refuses changes nothing, so a refused edit leaves this
    editor exactly as it was.  What one operation cannot see on its own -- a
    second parent, a cycle, a membership subset -- is caught by the single
    validation at freeze, which is the same validation a frozen graph runs.
    """

    def __init__(self, graph: Graph) -> None:
        """Copy one graph's content into carriers this editor may change."""
        self._source = graph
        self._displacement = Displacement.stationary(graph)
        self._namespaces = list(graph.namespaces)
        self._tiers = [
            _MutableTier(tier.declaration, list(tier.items), list(tier.attributes))
            for tier in graph.tiers
        ]
        self._relation_declarations = list(graph.relation_declarations)
        self._relations = list(graph.relations)
        self._attribute_declarations = list(graph.attribute_declarations)
        self._boundary_values = list(graph.boundary_values)
        self._attributes = list(graph.attributes)
        self._polyadic_relations = list(graph.polyadic_relations)
        self._seals = list(graph.seals)
        self._layers = list(graph.layers)

    def freeze(self) -> Graph:
        """Return a fully validated graph without consuming this editor."""
        return replace(
            self._source,
            namespaces=tuple(self._namespaces),
            tiers=tuple(
                Tier(tier.declaration, tuple(tier.items), tuple(tier.attributes))
                for tier in self._tiers
            ),
            relation_declarations=tuple(self._relation_declarations),
            relations=tuple(self._relations),
            attribute_declarations=tuple(self._attribute_declarations),
            boundary_values=tuple(self._boundary_values),
            attributes=tuple(self._attributes),
            polyadic_relations=tuple(self._polyadic_relations),
            seals=tuple(self._seals),
            layers=tuple(self._layers),
        )

    def displacement(self) -> Displacement:
        """Return where every position of this editor's input now stands."""
        return self._displacement

    def declare(self, declaration: EditDeclaration) -> GraphEditor:
        """Add one namespace, tier, attribute, or relation declaration.

        Declarations are added, never changed or withdrawn.  Retyping or
        withdrawing one retroactively decides the meaning of every value and
        reference that already depends on it, which is a migration of the
        whole graph rather than an edit to a place in it.
        """
        if isinstance(declaration, NamespaceDeclaration):
            self._namespaces.append(declaration)
        elif isinstance(declaration, TierDeclaration):
            self._tiers.append(_MutableTier(declaration, [], []))
        elif isinstance(declaration, AttributeDeclaration):
            self._attribute_declarations.append(declaration)
        elif isinstance(
            declaration,
            SimpleRelationDeclaration
            | BipartiteRelationDeclaration
            | PolyadicRelationDeclaration,
        ):
            self._relation_declarations.append(declaration)
        else:
            raise GraphValidationError(
                "declare expected a namespace, tier, attribute, or relation "
                f"declaration; got {type(declaration).__name__}"
            )
        return self

    def set_attribute(self, target: EditTarget, value: AttributeValue) -> GraphEditor:
        """Give one carrier this value, replacing any value of the same name.

        The value's declaration decides which carrier the target names, so a
        caller spells the place and not the domain.  An undeclared attribute
        is refused here rather than at freeze, because without a declaration
        there is no domain to read the target against.
        """
        domain = self._declared(value.name).domain
        if domain is AttributeDomain.DOCUMENT:
            _require_absent_target(target, domain)
            self._attributes = list(_with_value(self._attributes, value))
        elif domain is AttributeDomain.TIER:
            member = self._member(
                _require_named_target(target, domain), "tier attribute"
            )
            member.attributes = list(_with_value(member.attributes, value))
        elif domain is AttributeDomain.ITEM:
            coordinate = self._item_target(target, domain)
            member = self._member(coordinate.tier, "item attribute")
            item = member.items[coordinate.index]
            member.items[coordinate.index] = Item(
                item.durable_id, _with_value(item.attributes, value)
            )
        elif domain is AttributeDomain.BOUNDARY:
            reference = _require_boundary_target(target, domain)
            index = self._boundary_index(self._resolve_boundary(reference))
            if index is None:
                self._boundary_values.append(Boundary(reference, (value,)))
            else:
                stored = self._boundary_values[index]
                self._boundary_values[index] = Boundary(
                    stored.reference, _with_value(stored.attributes, value)
                )
        elif domain is AttributeDomain.RELATION_DECLARATION:
            name = _require_named_target(target, domain)
            index = self._declaration_index(name)
            self._relation_declarations[index] = _relation_declaration_with(
                self._relation_declarations[index],
                _with_value(self._relation_declarations[index].attributes, value),
            )
        else:
            polyadic, index = self._relation_site(target)
            self._set_relation_attributes(
                polyadic, index, _with_value(self._instance(polyadic, index), value)
            )
        return self

    def remove_attribute(self, target: EditTarget, name: QualifiedName) -> GraphEditor:
        """Take the named value off one carrier, refusing when it is absent."""
        domain = self._declared(name).domain
        if domain is AttributeDomain.DOCUMENT:
            _require_absent_target(target, domain)
            self._attributes = list(
                _without_value(self._attributes, name, "the document")
            )
        elif domain is AttributeDomain.TIER:
            qualified = _require_named_target(target, domain)
            member = self._member(qualified, "tier attribute")
            member.attributes = list(
                _without_value(member.attributes, name, f"tier {str(qualified)!r}")
            )
        elif domain is AttributeDomain.ITEM:
            coordinate = self._item_target(target, domain)
            member = self._member(coordinate.tier, "item attribute")
            item = member.items[coordinate.index]
            member.items[coordinate.index] = Item(
                item.durable_id,
                _without_value(item.attributes, name, f"item {str(coordinate)!r}"),
            )
        elif domain is AttributeDomain.BOUNDARY:
            reference = _require_boundary_target(target, domain)
            index = self._boundary_index(self._resolve_boundary(reference))
            if index is None:
                raise GraphValidationError(
                    f"boundary {str(reference)!r} carries no attribute {str(name)!r}"
                )
            stored = self._boundary_values[index]
            kept = _without_value(
                stored.attributes, name, f"boundary {str(stored.reference)!r}"
            )
            if kept:
                self._boundary_values[index] = Boundary(stored.reference, kept)
            else:
                del self._boundary_values[index]
        elif domain is AttributeDomain.RELATION_DECLARATION:
            qualified = _require_named_target(target, domain)
            index = self._declaration_index(qualified)
            declaration = self._relation_declarations[index]
            self._relation_declarations[index] = _relation_declaration_with(
                declaration,
                _without_value(
                    declaration.attributes, name, f"relation {str(qualified)!r}"
                ),
            )
        else:
            polyadic, index = self._relation_site(target)
            self._set_relation_attributes(
                polyadic,
                index,
                _without_value(
                    self._instance(polyadic, index),
                    name,
                    f"relation instance {index}",
                ),
            )
        return self

    def insert_item(self, tier: QualifiedName, index: int, item: Item) -> GraphEditor:
        """Insert one item at a tier index, carrying later references with it.

        An index equal to the tier's item count appends.
        """
        member = self._member(tier, "item insertion")
        count = len(member.items)
        if index < 0 or index > count:
            raise GraphValidationError(
                f"item insertion index {index} is outside tier {str(tier)!r}"
            )
        self._restructure(
            member,
            [*member.items[:index], item, *member.items[index:]],
            {old: old if old < index else old + 1 for old in range(count)},
            "item insertion",
        )
        return self

    def remove_item(self, reference: ItemRef | DurableItemRef) -> GraphEditor:
        """Remove one item, refusing while the graph still references it."""
        coordinate = self._resolve_item(reference)
        member = self._member(coordinate.tier, "item removal")
        count = len(member.items)
        self._restructure(
            member,
            [*member.items[: coordinate.index], *member.items[coordinate.index + 1 :]],
            {
                old: old if old < coordinate.index else old - 1
                for old in range(count)
                if old != coordinate.index
            },
            "item removal",
        )
        return self

    def move_item(self, reference: ItemRef | DurableItemRef, index: int) -> GraphEditor:
        """Move one item to another index of its own tier, carrying references.

        A move across tiers is not this operation.  Membership decides an
        item's type, so carrying an item into another tier retypes it, and a
        caller who means that says so with a removal and an insertion.
        """
        coordinate = self._resolve_item(reference)
        member = self._member(coordinate.tier, "item move")
        count = len(member.items)
        if index < 0 or index >= count:
            raise GraphValidationError(
                f"item move index {index} is outside tier {str(coordinate.tier)!r}"
            )
        items = list(member.items)
        items.insert(index, items.pop(coordinate.index))
        order = list(range(count))
        order.insert(index, order.pop(coordinate.index))
        self._restructure(
            member,
            items,
            {old: new for new, old in enumerate(order)},
            "item move",
        )
        return self

    def swap_items(
        self,
        first: ItemRef | DurableItemRef,
        second: ItemRef | DurableItemRef,
    ) -> GraphEditor:
        """Exchange two items of one tier, carrying their references with them."""
        left = self._resolve_item(first)
        right = self._resolve_item(second)
        if left.tier != right.tier:
            raise GraphValidationError(
                f"item swap names {str(left)!r} and {str(right)!r} in different "
                "tiers; an item's tier decides its type"
            )
        member = self._member(left.tier, "item swap")
        items = list(member.items)
        items[left.index], items[right.index] = items[right.index], items[left.index]
        mapping = {old: old for old in range(len(items))}
        mapping[left.index] = right.index
        mapping[right.index] = left.index
        self._restructure(member, items, mapping, "item swap")
        return self

    def add_relation(
        self, instance: RelationInstance | PolyadicRelationInstance
    ) -> GraphEditor:
        """Add one relation instance to the collection its arity belongs to."""
        if isinstance(instance, RelationInstance):
            self._relations.append(instance)
        else:
            self._polyadic_relations.append(instance)
        return self

    def remove_relation(self, target: int | str) -> GraphEditor:
        """Remove one relation instance by bipartite index or by durable id."""
        polyadic, index = self._relation_site(target)
        carrier = (
            GraphCarrier.POLYADIC_RELATIONS if polyadic else GraphCarrier.RELATIONS
        )
        seal = self._seal_for(carrier)
        if seal is not None and index < seal.sealed:
            self._refuse_seal_move("relation removal", carrier, index, seal.sealed)
        if polyadic:
            mapping = {
                old: old if old < index else old - 1
                for old in range(len(self._polyadic_relations))
                if old != index
            }
            step = self._current_displacement(
                polyadic_relations=mapping,
                departed_polyadic_relations=frozenset({index}),
            )
            self._layers = [_remap_layer(layer, step) for layer in self._layers]
            durable_id = self._polyadic_relations[index].durable_id
            if (
                durable_id is not None
            ):  # pragma: no branch - polyadic removal needs an id
                self._layers = [
                    _orphan_durable_relation(
                        layer, durable_id, GraphCarrier.POLYADIC_RELATIONS, index
                    )
                    for layer in self._layers
                ]
            del self._polyadic_relations[index]
        else:
            mapping = {
                old: old if old < index else old - 1
                for old in range(len(self._relations))
                if old != index
            }
            step = self._current_displacement(
                relations=mapping, departed_relations=frozenset({index})
            )
            self._layers = [_remap_layer(layer, step) for layer in self._layers]
            durable_id = self._relations[index].durable_id
            if durable_id is not None:
                self._layers = [
                    _orphan_durable_relation(
                        layer, durable_id, GraphCarrier.RELATIONS, index
                    )
                    for layer in self._layers
                ]
            del self._relations[index]
        self._advance_displacement(step)
        return self

    def _declared(self, name: QualifiedName) -> AttributeDeclaration:
        for declaration in self._attribute_declarations:
            if declaration.name == name:
                return declaration
        raise GraphValidationError(f"attribute {str(name)!r} is undeclared")

    def _member(self, name: QualifiedName, subject: str) -> _MutableTier:
        for member in self._tiers:
            if member.declaration.name == name:
                return member
        raise GraphValidationError(f"{subject} names undeclared tier {str(name)!r}")

    def _declaration_index(self, name: QualifiedName) -> int:
        for index, declaration in enumerate(self._relation_declarations):
            if declaration.name == name:
                return index
        raise GraphValidationError(f"relation {str(name)!r} is undeclared")

    def _instance(self, polyadic: bool, index: int) -> tuple[AttributeValue, ...]:
        if polyadic:
            return self._polyadic_relations[index].attributes
        return self._relations[index].attributes

    def _set_relation_attributes(
        self, polyadic: bool, index: int, attributes: tuple[AttributeValue, ...]
    ) -> None:
        if polyadic:
            relation = self._polyadic_relations[index]
            self._polyadic_relations[index] = PolyadicRelationInstance(
                relation.declaration,
                relation.sources,
                relation.targets,
                relation.durable_id,
                attributes,
            )
        else:
            instance = self._relations[index]
            self._relations[index] = RelationInstance(
                instance.declaration,
                instance.left,
                instance.right,
                instance.durable_id,
                attributes,
            )

    def _relation_site(self, target: EditTarget) -> tuple[bool, int]:
        if isinstance(target, int) and not isinstance(target, bool):
            if target < 0 or target >= len(self._relations):
                raise GraphValidationError(
                    f"relation instance index {target} is outside the graph's "
                    f"{len(self._relations)} bipartite relation instances"
                )
            return False, target
        if not isinstance(target, str):
            raise GraphValidationError(
                "relation instance target must be a bipartite instance index "
                "or a durable id"
            )
        for index, relation in enumerate(self._relations):
            if relation.durable_id == target:
                return False, index
        for index, polyadic_relation in enumerate(self._polyadic_relations):
            if polyadic_relation.durable_id == target:
                return True, index
        raise GraphValidationError(
            f"no relation instance carries durable id {target!r}"
        )

    def _tier_views(self) -> dict[QualifiedName, Tier]:
        # The validators inspect only declaration identity and item indexing.
        return cast(
            dict[QualifiedName, Tier],
            {member.declaration.name: member for member in self._tiers},
        )

    def _items_by_id(self) -> dict[str, ItemRef]:
        return {
            item.durable_id: ItemRef(member.declaration.name, index)
            for member in self._tiers
            for index, item in enumerate(member.items)
            if item.durable_id is not None
        }

    def _resolve_item(self, reference: ItemRef | DurableItemRef) -> ItemRef:
        if isinstance(reference, ItemRef):
            _validate_reference(
                reference, "item reference", self._tier_views(), GraphValidationError
            )
            return reference
        coordinate = self._items_by_id().get(reference.durable_id)
        if coordinate is None:
            raise GraphValidationError(
                f"unknown durable item id {reference.durable_id!r}"
            )
        return coordinate

    def _resolve_boundary(
        self, reference: BoundaryRef | DurableBoundaryRef
    ) -> BoundaryRef:
        return _resolve_boundary_reference(
            reference, self._tier_views(), self._items_by_id(), GraphValidationError
        )

    def _boundary_index(self, coordinate: BoundaryRef) -> int | None:
        for index, boundary in enumerate(self._boundary_values):
            if self._resolve_boundary(boundary.reference) == coordinate:
                return index
        return None

    def _item_target(self, target: EditTarget, domain: AttributeDomain) -> ItemRef:
        if not isinstance(target, ItemRef | DurableItemRef):
            raise GraphValidationError(
                f"{domain.value} attribute target must be an item reference"
            )
        return self._resolve_item(target)

    def _restructure(
        self,
        member: _MutableTier,
        items: list[Item],
        mapping: dict[int, int],
        subject: str,
    ) -> None:
        # Everything a refusal can see is computed before anything is written,
        # so a refused operation leaves this editor exactly as it was.
        name = member.declaration.name
        seal = self._seal_for(name)
        if seal is not None:
            moved = next(
                (old for old in range(seal.sealed) if mapping.get(old) != old),
                None,
            )
            if moved is not None:
                self._refuse_seal_move(subject, name, moved, seal.sealed)
        images = _boundary_images(len(member.items), len(items), mapping)
        boundaries = [
            self._remapped_boundary(boundary, name, images, subject)
            for boundary in self._boundary_values
        ]
        relations = [
            self._remapped_relation(relation, name, mapping, subject)
            for relation in self._relations
        ]
        polyadic = [
            self._remapped_polyadic(relation, name, mapping, subject)
            for relation in self._polyadic_relations
        ]
        item_mapping = {
            ItemRef(name, old): ItemRef(name, new) for old, new in mapping.items()
        }
        departed_items = frozenset(
            ItemRef(name, old) for old in range(len(member.items)) if old not in mapping
        )
        boundary_mapping = {
            BoundaryRef(name, old): BoundaryRef(name, new)
            for old, new in images.items()
        }
        departed_boundaries = frozenset(
            BoundaryRef(name, old)
            for old in range(len(member.items) + 1)
            if old not in images
        )
        step = self._current_displacement(
            items=item_mapping,
            boundaries=boundary_mapping,
            departed_items=departed_items,
            departed_boundaries=departed_boundaries,
        )
        layers = [_remap_layer(layer, step) for layer in self._layers]
        for coordinate in departed_items:
            durable_id = member.items[coordinate.index].durable_id
            if durable_id is not None:
                layers = [
                    _orphan_durable_item(layer, durable_id, coordinate)
                    for layer in layers
                ]
        member.items = items
        self._boundary_values = boundaries
        self._relations = relations
        self._polyadic_relations = polyadic
        self._layers = layers
        self._advance_displacement(step)

    def _advance_displacement(self, step: Displacement) -> None:
        """Accumulate one editor step with the helper used by ``then``."""
        current = self._displacement
        items, departed_items = _compose_displacement_space(
            current.items, current.departed_items, step.items, step.departed_items
        )
        boundaries, departed_boundaries = _compose_displacement_space(
            current.boundaries,
            current.departed_boundaries,
            step.boundaries,
            step.departed_boundaries,
        )
        relations, departed_relations = _compose_displacement_space(
            current.relations,
            current.departed_relations,
            step.relations,
            step.departed_relations,
        )
        polyadic_relations, departed_polyadic_relations = _compose_displacement_space(
            current.polyadic_relations,
            current.departed_polyadic_relations,
            step.polyadic_relations,
            step.departed_polyadic_relations,
        )
        self._displacement = Displacement(
            items,
            boundaries,
            relations,
            polyadic_relations,
            departed_items,
            departed_boundaries,
            departed_relations,
            departed_polyadic_relations,
        )

    def _current_displacement(
        self,
        *,
        items: Mapping[ItemRef, ItemRef] | None = None,
        boundaries: Mapping[BoundaryRef, BoundaryRef] | None = None,
        relations: Mapping[int, int] | None = None,
        polyadic_relations: Mapping[int, int] | None = None,
        departed_items: frozenset[ItemRef] = frozenset(),
        departed_boundaries: frozenset[BoundaryRef] = frozenset(),
        departed_relations: frozenset[int] = frozenset(),
        departed_polyadic_relations: frozenset[int] = frozenset(),
    ) -> Displacement:
        """Build one operation's total displacement from the current editor."""
        item_coordinates = tuple(
            ItemRef(member.declaration.name, index)
            for member in self._tiers
            for index in range(len(member.items))
        )
        boundary_coordinates = tuple(
            BoundaryRef(member.declaration.name, index)
            for member in self._tiers
            for index in range(len(member.items) + 1)
        )
        return Displacement(
            dict(_identity_except(item_coordinates, items, departed_items)),
            dict(
                _identity_except(boundary_coordinates, boundaries, departed_boundaries)
            ),
            dict(
                _identity_except(
                    range(len(self._relations)), relations, departed_relations
                )
            ),
            dict(
                _identity_except(
                    range(len(self._polyadic_relations)),
                    polyadic_relations,
                    departed_polyadic_relations,
                )
            ),
            departed_items,
            departed_boundaries,
            departed_relations,
            departed_polyadic_relations,
        )

    def _seal_for(self, carrier: SealedCarrier) -> Seal | None:
        return next((seal for seal in self._seals if seal.carrier == carrier), None)

    @staticmethod
    def _refuse_seal_move(
        subject: str, carrier: SealedCarrier, index: int, sealed: int
    ) -> None:
        operation = (
            f"{subject} at {str(carrier)!r}[{index}]"
            if subject == "item insertion"
            else subject
        )
        raise GraphValidationError(
            f"{operation} would move {str(carrier)!r}[{index}], which stands inside "
            f"this graph's seal on that {_carrier_kind(carrier)} at {sealed}. A seal "
            "says the coordinates up to it do not move, so an edit that moves one "
            "is not an edit this graph admits. Unseal that carrier first if the "
            "base itself is what needs correcting."
        )

    @staticmethod
    def _remapped_boundary(
        boundary: Boundary,
        name: QualifiedName,
        images: dict[int, int],
        subject: str,
    ) -> Boundary:
        reference = boundary.reference
        if not isinstance(reference, BoundaryRef) or reference.tier != name:
            return boundary
        image = images.get(reference.index)
        if image is None:
            raise GraphValidationError(
                f"{subject} leaves boundary value {str(reference)!r} without one "
                "boundary to hold it; promote the boundary so it follows its anchor"
            )
        return Boundary(BoundaryRef(name, image), boundary.attributes)

    @staticmethod
    def _remapped_endpoint(
        endpoint: RelationEndpointRef,
        name: QualifiedName,
        mapping: dict[int, int],
        subject: str,
    ) -> RelationEndpointRef:
        if not isinstance(endpoint, ItemRef) or endpoint.tier != name:
            return endpoint
        image = mapping.get(endpoint.index)
        if image is None:
            raise GraphValidationError(
                f"{subject} would drop {str(endpoint)!r}, which the graph still "
                "references"
            )
        return ItemRef(name, image)

    def _remapped_relation(
        self,
        relation: RelationInstance,
        name: QualifiedName,
        mapping: dict[int, int],
        subject: str,
    ) -> RelationInstance:
        left = self._remapped_endpoint(relation.left, name, mapping, subject)
        right = self._remapped_endpoint(relation.right, name, mapping, subject)
        if left is relation.left and right is relation.right:
            return relation
        return RelationInstance(
            relation.declaration, left, right, relation.durable_id, relation.attributes
        )

    def _remapped_polyadic(
        self,
        relation: PolyadicRelationInstance,
        name: QualifiedName,
        mapping: dict[int, int],
        subject: str,
    ) -> PolyadicRelationInstance:
        sources = tuple(
            self._remapped_endpoint(endpoint, name, mapping, subject)
            for endpoint in relation.sources
        )
        targets = tuple(
            self._remapped_endpoint(endpoint, name, mapping, subject)
            for endpoint in relation.targets
        )
        if sources == relation.sources and targets == relation.targets:
            return relation
        return PolyadicRelationInstance(
            relation.declaration,
            sources,
            targets,
            relation.durable_id,
            relation.attributes,
        )


def _boundary_images(
    old_count: int, new_count: int, mapping: dict[int, int]
) -> dict[int, int]:
    """Map each old boundary to the one new boundary that still means it.

    A boundary is the place between the items on either side of it, so it
    survives an edit exactly when both of those items survive and are still
    adjacent afterward.  A boundary with no such image, or with more than one,
    is left out and refuses any stored value that sits there.
    """
    images: dict[int, int] = {}
    for boundary in range(old_count + 1):
        left = None if boundary == 0 else mapping.get(boundary - 1)
        right = None if boundary == old_count else mapping.get(boundary)
        if boundary > 0 and left is None:
            continue
        if boundary < old_count and right is None:
            continue
        if left is None and right is None:
            continue
        if left is None:
            if right == 0:
                images[boundary] = 0
        elif right is None:
            if left == new_count - 1:
                images[boundary] = new_count
        elif right == left + 1:
            images[boundary] = right
    return images


type _Coordinate = ItemRef | BoundaryRef | int


def _identity_except[Coordinate: _Coordinate](
    coordinates: Iterable[Coordinate],
    replacements: Mapping[Coordinate, Coordinate] | None,
    departed: frozenset[Coordinate],
) -> Iterable[tuple[Coordinate, Coordinate]]:
    """Map every coordinate to itself except where replacements say otherwise."""
    replacement_map = {} if replacements is None else replacements
    return (
        (coordinate, replacement_map.get(coordinate, coordinate))
        for coordinate in coordinates
        if coordinate not in departed
    )


def _compose_displacement_space[Coordinate: _Coordinate](
    earlier: Mapping[Coordinate, Coordinate],
    earlier_departed: frozenset[Coordinate],
    later: Mapping[Coordinate, Coordinate],
    later_departed: frozenset[Coordinate],
) -> tuple[dict[Coordinate, Coordinate], frozenset[Coordinate]]:
    """Compose one total positional map, refusing an unrelated later domain."""
    composed: dict[Coordinate, Coordinate] = {}
    departed = set(earlier_departed)
    for source, intermediate in earlier.items():
        if intermediate in later:
            composed[source] = later[intermediate]
        elif intermediate in later_departed:
            departed.add(source)
        else:
            raise GraphValidationError(
                f"displacement composition names {str(intermediate)!r} as an image, "
                "and the later displacement is not about a graph that has it; a "
                "composition is defined only where the first displacement's result "
                "is the second's source"
            )
    return composed, frozenset(departed)


def _with_value(
    values: Iterable[AttributeValue], value: AttributeValue
) -> tuple[AttributeValue, ...]:
    return (
        *(existing for existing in values if existing.name != value.name),
        value,
    )


def _without_value(
    values: Iterable[AttributeValue], name: QualifiedName, subject: str
) -> tuple[AttributeValue, ...]:
    remaining = tuple(values)
    kept = tuple(existing for existing in remaining if existing.name != name)
    if len(kept) == len(remaining):
        raise GraphValidationError(
            f"{subject} carries no attribute {str(name)!r} to remove"
        )
    return kept


def _relation_declaration_with(
    declaration: RelationDeclaration, attributes: tuple[AttributeValue, ...]
) -> RelationDeclaration:
    if isinstance(declaration, SimpleRelationDeclaration):
        return SimpleRelationDeclaration(
            declaration.name, declaration.tier, declaration.item_type, attributes
        )
    if isinstance(declaration, BipartiteRelationDeclaration):
        return BipartiteRelationDeclaration(
            declaration.name,
            declaration.left_type,
            declaration.right_type,
            declaration.left_endpoint,
            declaration.right_endpoint,
            declaration.single_parent,
            declaration.acyclic,
            attributes,
        )
    return PolyadicRelationDeclaration(
        declaration.name,
        declaration.sources,
        declaration.targets,
        declaration.unique_sources,
        declaration.distinct_targets,
        declaration.single_parent,
        declaration.acyclic,
        declaration.targets_subset_of,
        attributes,
    )


def _require_absent_target(target: EditTarget, domain: AttributeDomain) -> None:
    if target is not None:
        raise GraphValidationError(f"{domain.value} attribute target must be None")


def _require_named_target(target: EditTarget, domain: AttributeDomain) -> QualifiedName:
    if not isinstance(target, QualifiedName):
        raise GraphValidationError(
            f"{domain.value} attribute target must be a qualified name"
        )
    return target


def _require_boundary_target(
    target: EditTarget, domain: AttributeDomain
) -> BoundaryRef | DurableBoundaryRef:
    if not isinstance(target, BoundaryRef | DurableBoundaryRef):
        raise GraphValidationError(
            f"{domain.value} attribute target must be a boundary reference"
        )
    return target


@dataclass(slots=True)
class _MutableTier:
    declaration: TierDeclaration
    items: list[Item]
    attributes: list[AttributeValue]


class _GraphBuilder:
    """Accumulate trusted machine state before one complete graph validation."""

    def __init__(self) -> None:
        self.namespaces: list[NamespaceDeclaration] = []
        self.tiers: list[_MutableTier] = []
        self.relation_declarations: list[RelationDeclaration] = []
        self.relations: list[RelationInstance] = []
        self.attribute_declarations: list[AttributeDeclaration] = []
        self.boundary_values: list[Boundary] = []
        self.attributes: list[AttributeValue] = []
        self.polyadic_relations: list[PolyadicRelationInstance] = []
        self.declared_namespaces: set[str] = set()
        self.tiers_by_name: dict[QualifiedName, _MutableTier] = {}
        self.items_by_id: dict[str, ItemRef] = {}
        self.types_by_tier: dict[QualifiedName, QualifiedName] = {}
        self.declarations_by_name: dict[QualifiedName, RelationDeclaration] = {}
        self.declaration_indexes: dict[QualifiedName, int] = {}
        self.attributes_by_name: dict[QualifiedName, AttributeDeclaration] = {}
        self.boundaries_by_coordinate: dict[BoundaryRef, int] = {}
        self.after_boundary_by_tier: dict[QualifiedName, int] = {}
        self.polyadic_targets_by_source: dict[
            tuple[QualifiedName, ItemRef | BoundaryRef],
            set[ItemRef | BoundaryRef],
        ] = {}

    def _tier_views(self) -> dict[QualifiedName, Tier]:
        # The validators inspect only declaration identity and item count/indexing.
        return cast(dict[QualifiedName, Tier], self.tiers_by_name)

    def _require_namespaces(self, names: Iterable[QualifiedName]) -> None:
        for name in names:
            if name.namespace not in self.declared_namespaces:
                raise ValueError(
                    f"qualified name {str(name)!r} uses undeclared namespace {name.namespace!r}"
                )

    def _resolve_item(self, reference: ItemRef | DurableItemRef) -> ItemRef:
        if isinstance(reference, ItemRef):
            _validate_reference(
                reference, "item reference", self._tier_views(), ValueError
            )
            return reference
        coordinate = self.items_by_id.get(reference.durable_id)
        if coordinate is None:
            raise ValueError(f"unknown durable item id {reference.durable_id!r}")
        return coordinate

    def _resolve_boundary(
        self, reference: BoundaryRef | DurableBoundaryRef
    ) -> BoundaryRef:
        return _resolve_boundary_reference(
            reference, self._tier_views(), self.items_by_id, ValueError
        )

    def _finish(self) -> Graph:
        return replace(
            _EMPTY_GRAPH,
            namespaces=tuple(self.namespaces),
            tiers=tuple(
                Tier(tier.declaration, tuple(tier.items), tuple(tier.attributes))
                for tier in self.tiers
            ),
            relation_declarations=tuple(self.relation_declarations),
            relations=tuple(self.relations),
            attribute_declarations=tuple(self.attribute_declarations),
            boundary_values=tuple(self.boundary_values),
            attributes=tuple(self.attributes),
            polyadic_relations=tuple(self.polyadic_relations),
        )


def _canonical_lexical(value_type: XsdType, lexical: str) -> str:
    # xsd:string has whiteSpace=preserve; the remaining admitted types use collapse.
    if value_type is XsdType.STRING:
        return lexical
    lexical = re.sub(r"[ \t\r\n]+", " ", lexical).strip(" ")
    if value_type is XsdType.BOOLEAN:
        if lexical in {"true", "1"}:
            return "true"
        if lexical in {"false", "0"}:
            return "false"
        raise ValueError(lexical)
    if value_type is XsdType.INTEGER:
        if _INTEGER_LEXICAL.fullmatch(lexical) is None:
            raise ValueError(lexical)
        return str(int(lexical))
    if value_type is XsdType.DECIMAL:
        if _DECIMAL_LEXICAL.fullmatch(lexical) is None:
            raise ValueError(lexical)
        value = Decimal(lexical)
        if value.is_zero():
            return "0.0"
        fixed = format(value, "f")
        whole, separator, fraction = fixed.partition(".")
        fraction = fraction.rstrip("0") or "0"
        if not separator:
            fraction = "0"
        return f"{whole}.{fraction}"
    if _DOUBLE_LEXICAL.fullmatch(lexical) is None:
        raise ValueError(lexical)
    if lexical == "NaN":
        return "NaN"
    if lexical in {"INF", "+INF"}:
        return "INF"
    if lexical == "-INF":
        return "-INF"
    double_value = float(lexical)
    if math.isinf(double_value):
        return "-INF" if double_value < 0 else "INF"
    if double_value == 0.0:
        return "-0.0E0" if math.copysign(1.0, double_value) < 0 else "0.0E0"
    shortest = repr(double_value).lower()
    coefficient, _, exponent_text = shortest.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    sign = ""
    if coefficient.startswith("-"):
        sign, coefficient = "-", coefficient[1:]
    whole, _, fraction = coefficient.partition(".")
    digits = (whole + fraction).lstrip("0")
    first_nonzero = (
        next(index for index, digit in enumerate(whole) if digit != "0")
        if int(whole)
        else None
    )
    if first_nonzero is not None:
        scientific_exponent = exponent + len(whole) - first_nonzero - 1
    else:
        leading_fraction_zeros = len(fraction) - len(fraction.lstrip("0"))
        scientific_exponent = exponent - leading_fraction_zeros - 1
    mantissa_tail = digits[1:].rstrip("0") or "0"
    return f"{sign}{digits[0]}.{mantissa_tail}E{scientific_exponent}"


def _attributes_data(attributes: tuple[AttributeValue, ...]) -> list[JsonValue]:
    return [attribute.to_data() for attribute in attributes]


class _AttributeCarrier(Protocol):
    @property
    def attributes(self) -> tuple[AttributeValue, ...]:
        """Return the carrier's attribute values, in canonical name order."""
        ...


def _canonicalize_attributes(value: _AttributeCarrier) -> None:
    attributes = value.attributes
    object.__setattr__(
        value, "attributes", tuple(sorted(attributes, key=lambda item: item.name))
    )


def _require_name(value: str, subject: str) -> None:
    if not value:
        raise GraphValidationError(f"{subject} {value!r} must not be empty")


def _unique_by_name[NameKey, NamedValue](
    pairs: Iterable[tuple[NameKey, NamedValue]], subject: str
) -> dict[NameKey, NamedValue]:
    result: dict[NameKey, NamedValue] = {}
    for name, value in pairs:
        if name in result:
            raise GraphValidationError(
                f"duplicate {subject} {str(name)!r}; names must be unique"
            )
        result[name] = value
    return result


def _duplicate(values: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _require_unique_durable_ids(values: Iterable[tuple[str, str]]) -> None:
    sources: dict[str, str] = {}
    for durable_id, source in values:
        previous = sources.get(durable_id)
        if previous is not None:
            raise GraphValidationError(
                f"duplicate durable id {durable_id!r}; {previous} collides with {source}"
            )
        sources[durable_id] = source


def _validate_attributes(
    values: tuple[AttributeValue, ...],
    domain: AttributeDomain,
    declarations: Mapping[QualifiedName, AttributeDeclaration],
) -> None:
    _unique_by_name(((value.name, value) for value in values), "attribute value")
    for value in values:
        candidate = declarations.get(value.name)
        if not isinstance(candidate, AttributeDeclaration):
            raise GraphValidationError(f"attribute {str(value.name)!r} is undeclared")
        if candidate.domain is not domain:
            raise GraphValidationError(
                f"attribute {str(value.name)!r} has domain {candidate.domain.value!r}; "
                f"it cannot occur on {domain.value!r}"
            )
        if candidate.value_type is not value.value_type:
            raise GraphValidationError(
                f"attribute {str(value.name)!r} has type {candidate.value_type.value!r}; "
                f"value has type {value.value_type.value!r}"
            )


def _unique_simple_types(
    declarations: list[SimpleRelationDeclaration],
    tiers: Mapping[QualifiedName, Tier],
) -> dict[QualifiedName, QualifiedName]:
    result: dict[QualifiedName, QualifiedName] = {}
    for declaration in declarations:
        if declaration.tier not in tiers:
            raise GraphValidationError(
                f"simple relation {str(declaration.name)!r} names undeclared tier "
                f"{str(declaration.tier)!r}"
            )
        if declaration.tier in result:
            raise GraphValidationError(
                f"tier {str(declaration.tier)!r} has multiple simple relations; at most one is allowed"
            )
        result[declaration.tier] = declaration.item_type
    return result


def _validate_reference(
    reference: ItemRef,
    subject: str,
    tiers: dict[QualifiedName, Tier],
    error_type: type[ValueError],
) -> None:
    tier = tiers.get(reference.tier)
    if tier is None:
        raise error_type(f"{subject} names undeclared tier {str(reference.tier)!r}")
    if reference.index < 0 or reference.index >= len(tier.items):
        raise error_type(
            f"{subject} {str(reference)!r} is outside tier {str(reference.tier)!r}"
        )


def _validate_boundary(
    reference: BoundaryRef,
    tiers: dict[QualifiedName, Tier],
    error_type: type[ValueError],
) -> None:
    tier = tiers.get(reference.tier)
    if tier is None:
        raise error_type(
            f"boundary {str(reference)!r} names undeclared tier {str(reference.tier)!r}"
        )
    if reference.index < 0 or reference.index > len(tier.items):
        raise error_type(
            f"boundary {str(reference)!r} is outside tier {str(reference.tier)!r}"
        )


def _resolve_boundary_reference(
    reference: BoundaryRef | DurableBoundaryRef,
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
    error_type: type[ValueError],
) -> BoundaryRef:
    if isinstance(reference, BoundaryRef):
        _validate_boundary(reference, tiers, error_type)
        return reference
    if isinstance(reference.anchor, QualifiedName):
        tier = tiers.get(reference.anchor)
        if tier is None:
            raise error_type(
                f"durable boundary tier anchor {str(reference.anchor)!r} is not declared"
            )
        return BoundaryRef(
            reference.anchor,
            0 if reference.side is BoundarySide.BEFORE else len(tier.items),
        )
    coordinate = items_by_id.get(reference.anchor.durable_id)
    if coordinate is None:
        raise error_type(
            f"durable boundary anchor item {reference.anchor.durable_id!r} was not found"
        )
    return BoundaryRef(
        coordinate.tier,
        coordinate.index
        if reference.side is BoundarySide.BEFORE
        else coordinate.index + 1,
    )


def _require_integral_index(index: object, subject: str, offender: object) -> None:
    if isinstance(index, bool) or not isinstance(index, int):
        raise GraphValidationError(
            f"{subject} {offender!r} has non-integral index {index!r}"
        )


def _require_integral_bound(value: object, subject: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GraphValidationError(f"{subject} {value!r} must be a nonnegative integer")


def _require_boolean(value: object, subject: str) -> None:
    if not isinstance(value, bool):
        raise GraphValidationError(f"{subject} {value!r} must be boolean")


def _validate_endpoint(
    relation_index: int,
    side: str,
    reference: RelationEndpointRef,
    expected_type: QualifiedName,
    expected_kind: RelationEndpointKind,
    tiers: dict[QualifiedName, Tier],
    types: dict[QualifiedName, QualifiedName],
    items_by_id: dict[str, ItemRef],
) -> None:
    subject = f"relation instance {relation_index} {side} endpoint"
    actual_kind = (
        RelationEndpointKind.ITEM
        if isinstance(reference, ItemRef | DurableItemRef)
        else RelationEndpointKind.BOUNDARY
    )
    if actual_kind is not expected_kind:
        article = "an" if actual_kind is RelationEndpointKind.ITEM else "a"
        expected_article = "an" if expected_kind is RelationEndpointKind.ITEM else "a"
        raise GraphValidationError(
            f"{subject} {str(reference)!r} is {article} {actual_kind.value}; "
            f"declaration requires {expected_article} {expected_kind.value}"
        )
    if isinstance(reference, ItemRef):
        _validate_reference(reference, subject, tiers, GraphValidationError)
        tier_name = reference.tier
    elif isinstance(reference, DurableItemRef):
        coordinate = items_by_id.get(reference.durable_id)
        if coordinate is None:
            raise GraphValidationError(
                f"{subject} {reference.durable_id!r} names no item; a durable coordinate "
                "resolves against this graph's items and nothing carries that identifier"
            )
        tier_name = coordinate.tier
    else:
        tier_name = _boundary_anchor_tier(reference, subject, tiers, items_by_id)
    actual_type = types.get(tier_name)
    if actual_type is None:
        raise GraphValidationError(
            f"{subject} {str(reference)!r} belongs to untyped tier {str(tier_name)!r}"
        )
    if actual_type != expected_type:
        raise GraphValidationError(
            f"{subject} {str(reference)!r} has type {str(actual_type)!r}; "
            f"expected {str(expected_type)!r}"
        )


def _boundary_anchor_tier(
    reference: DurableBoundaryRef,
    subject: str,
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
) -> QualifiedName:
    if isinstance(reference.anchor, QualifiedName):
        if reference.anchor not in tiers:
            raise GraphValidationError(
                f"{subject} {str(reference)!r} names undeclared tier "
                f"{str(reference.anchor)!r}"
            )
        return reference.anchor
    coordinate = items_by_id.get(reference.anchor.durable_id)
    if coordinate is None:
        raise GraphValidationError(
            f"{subject} {str(reference)!r} names missing anchor item "
            f"{reference.anchor.durable_id!r}"
        )
    return coordinate.tier


def _validate_relation_invariants(
    relations: tuple[RelationInstance, ...],
    declarations: dict[QualifiedName, BipartiteRelationDeclaration],
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
) -> None:
    for name, declaration in declarations.items():
        indexed = [
            (index, edge)
            for index, edge in enumerate(relations)
            if edge.declaration == name
        ]
        resolved = [
            (
                index,
                edge,
                _resolve_relation_endpoint(
                    edge.left, tiers, items_by_id, GraphValidationError
                ),
                _resolve_relation_endpoint(
                    edge.right, tiers, items_by_id, GraphValidationError
                ),
            )
            for index, edge in indexed
        ]
        if declaration.single_parent:
            parents: dict[ItemRef | BoundaryRef, tuple[int, ItemRef | BoundaryRef]] = {}
            for index, edge, left, right in resolved:
                previous = parents.get(right)
                if previous is not None and previous[1] != left:
                    raise GraphValidationError(
                        f"relation instance {index} gives {str(edge.right)!r} a second "
                        f"parent in {str(name)!r}; first parent is relation instance {previous[0]}"
                    )
                parents[right] = (index, left)
        if declaration.acyclic:
            _require_acyclic(name, resolved)


def _validate_polyadic_instance(
    index: int,
    relation: PolyadicRelationInstance,
    declaration: PolyadicRelationDeclaration,
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
) -> None:
    for label, endpoints, side in (
        ("source", relation.sources, declaration.sources),
        ("target", relation.targets, declaration.targets),
    ):
        if not endpoints:
            if not side.allow_empty:
                raise GraphValidationError(
                    f"relation instance {index} has an empty {label} side"
                )
            continue
        if len(endpoints) < side.minimum or (
            side.maximum is not None and len(endpoints) > side.maximum
        ):
            raise GraphValidationError(
                f"relation instance {index} {label} arity {len(endpoints)} is outside "
                f"declared bounds {side.minimum}..{side.maximum}"
            )
        for endpoint_index, endpoint in enumerate(endpoints):
            subject = f"relation instance {index} {label} endpoint {endpoint_index}"
            kind = (
                RelationEndpointKind.ITEM
                if isinstance(endpoint, ItemRef | DurableItemRef)
                else RelationEndpointKind.BOUNDARY
            )
            if kind not in side.endpoint_kinds:
                raise GraphValidationError(
                    f"{subject} {str(endpoint)!r} has kind {kind.value!r}; "
                    "kind is not allowed by the declaration"
                )
            if isinstance(endpoint, ItemRef):
                _validate_reference(endpoint, subject, tiers, GraphValidationError)
                tier = endpoint.tier
            elif isinstance(endpoint, DurableItemRef):
                coordinate = items_by_id.get(endpoint.durable_id)
                if coordinate is None:
                    raise GraphValidationError(
                        f"{subject} {endpoint.durable_id!r} names no item; a durable "
                        "coordinate resolves against this graph's items and nothing "
                        "carries that identifier"
                    )
                tier = coordinate.tier
            else:
                tier = _boundary_anchor_tier(endpoint, subject, tiers, items_by_id)
            if side.tiers is not None and tier not in side.tiers:
                raise GraphValidationError(
                    f"{subject} {str(endpoint)!r} belongs to tier {str(tier)!r}; "
                    "tier is not allowed by the declaration"
                )


def _validate_polyadic_invariants(
    relations: tuple[PolyadicRelationInstance, ...],
    declarations: dict[QualifiedName, PolyadicRelationDeclaration],
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
) -> None:
    """Check promises on hyperedges without reducing composites to one factor.

    A polyadic instance is one parent even when its source composite has several
    endpoints. Membership, conversely, is the union of every base instance for
    a source: no source-uniqueness promise is required of a membership base.
    """
    grouped: dict[QualifiedName, list[tuple[int, PolyadicRelationInstance]]] = {
        name: [] for name in declarations
    }
    for index, relation in enumerate(relations):
        grouped[relation.declaration].append((index, relation))
    first_source_instance: dict[tuple[QualifiedName, ItemRef | BoundaryRef], int] = {}
    targets_by_source: dict[
        tuple[QualifiedName, ItemRef | BoundaryRef],
        set[ItemRef | BoundaryRef],
    ] = {}
    for name, declaration in declarations.items():
        resolved_edges: list[
            tuple[int, RelationInstance, ItemRef | BoundaryRef, ItemRef | BoundaryRef]
        ] = []
        resolved_instances: list[
            tuple[
                int,
                tuple[ItemRef | BoundaryRef, ...],
                tuple[ItemRef | BoundaryRef, ...],
            ]
        ] = []
        for index, relation in grouped[name]:
            resolved_sources = tuple(
                _resolve_relation_endpoint(
                    endpoint, tiers, items_by_id, GraphValidationError
                )
                for endpoint in relation.sources
            )
            resolved_targets = tuple(
                _resolve_relation_endpoint(
                    endpoint, tiers, items_by_id, GraphValidationError
                )
                for endpoint in relation.targets
            )
            if declaration.distinct_targets and len(set(resolved_targets)) != len(
                resolved_targets
            ):
                raise GraphValidationError(
                    f"relation instance {index} has duplicate declared-distinct targets"
                )
            resolved_instances.append((index, resolved_sources, resolved_targets))
            for source in resolved_sources:
                key = (name, source)
                previous = first_source_instance.get(key)
                if declaration.unique_sources and previous is not None:
                    raise GraphValidationError(
                        f"relation instance {index} repeats source {str(source)!r} in "
                        f"unique-source relation {str(name)!r}; first used by relation instance {previous}"
                    )
                first_source_instance.setdefault(key, index)
                targets_by_source.setdefault(key, set()).update(resolved_targets)
            resolved_edges.extend(
                (
                    index,
                    RelationInstance(name, relation.sources[0], relation.targets[0]),
                    source,
                    target,
                )
                for source in resolved_sources
                for target in resolved_targets
            )
        if declaration.single_parent:
            parents: dict[
                ItemRef | BoundaryRef,
                tuple[int, tuple[ItemRef | BoundaryRef, ...]],
            ] = {}
            for index, sources, targets in resolved_instances:
                for target in targets:
                    poly_previous = parents.get(target)
                    if poly_previous is not None and poly_previous[1] != sources:
                        raise GraphValidationError(
                            f"relation instance {index} gives {str(target)!r} a second parent "
                            f"in {str(name)!r}; first parent is relation instance {poly_previous[0]}"
                        )
                    parents[target] = (index, sources)
        if declaration.acyclic:
            _require_acyclic(name, resolved_edges)
    for name, declaration in declarations.items():
        if declaration.targets_subset_of is None:
            continue
        if declaration.targets_subset_of not in declarations:
            raise GraphValidationError(
                f"polyadic relation {str(name)!r} targets-subset-of names undeclared "
                f"polyadic relation {str(declaration.targets_subset_of)!r}"
            )
        for index, relation in grouped[name]:
            for source_ref in relation.sources:
                source = _resolve_relation_endpoint(
                    source_ref, tiers, items_by_id, GraphValidationError
                )
                allowed = targets_by_source.get((declaration.targets_subset_of, source))
                if allowed is None:
                    raise GraphValidationError(
                        f"relation instance {index} source {str(source)!r} has no "
                        f"{str(declaration.targets_subset_of)!r} membership relation"
                    )
                if any(
                    _resolve_relation_endpoint(
                        endpoint, tiers, items_by_id, GraphValidationError
                    )
                    not in allowed
                    for endpoint in relation.targets
                ):
                    raise GraphValidationError(
                        f"relation instance {index} has a target outside "
                        f"{str(declaration.targets_subset_of)!r} membership"
                    )


def _resolve_relation_endpoint(
    reference: RelationEndpointRef,
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
    error_type: type[ValueError],
) -> ItemRef | BoundaryRef:
    """Resolve boundary spellings to graph places before invariant comparison."""
    if isinstance(reference, ItemRef):
        return reference
    if isinstance(reference, DurableItemRef):
        coordinate = items_by_id.get(reference.durable_id)
        if coordinate is None:
            raise error_type(f"unknown durable item id {reference.durable_id!r}")
        return coordinate
    return _resolve_boundary_reference(reference, tiers, items_by_id, error_type)


def _require_acyclic(
    name: QualifiedName,
    indexed: list[
        tuple[int, RelationInstance, ItemRef | BoundaryRef, ItemRef | BoundaryRef]
    ],
) -> None:
    outgoing: dict[ItemRef | BoundaryRef, list[tuple[int, ItemRef | BoundaryRef]]] = {}
    for index, _edge, left, right in indexed:
        outgoing.setdefault(left, []).append((index, right))
    visited: set[ItemRef | BoundaryRef] = set()
    for root in tuple(outgoing):
        if root in visited:
            continue
        visiting: set[ItemRef | BoundaryRef] = {root}
        stack: list[tuple[ItemRef | BoundaryRef, int]] = [(root, 0)]
        while stack:
            node, child_index = stack[-1]
            children = outgoing.get(node, [])
            if child_index == len(children):
                stack.pop()
                visiting.remove(node)
                visited.add(node)
                continue
            edge_index, child = children[child_index]
            stack[-1] = (node, child_index + 1)
            if child in visiting:
                raise GraphValidationError(
                    f"relation instance {edge_index} closes a cycle in acyclic relation "
                    f"{str(name)!r} at {str(child)!r}"
                )
            if child not in visited:
                visiting.add(child)
                stack.append((child, 0))


def _sealed_carrier_key(carrier: SealedCarrier) -> tuple[int, str, str]:
    """Return one total order across tier and graph-wide carriers."""
    if isinstance(carrier, QualifiedName):
        return (0, carrier.namespace, carrier.local_name)
    return (1, carrier.value, "")


def _layer_subject_domain(subject: LayerSubject) -> AttributeDomain:
    if isinstance(subject, (ItemRef, DurableItemRef)):
        return AttributeDomain.ITEM
    if isinstance(subject, (BoundaryRef, DurableBoundaryRef)):
        return AttributeDomain.BOUNDARY
    if isinstance(subject, TierRef):
        return AttributeDomain.TIER
    if isinstance(subject, RelationDeclarationRef):
        return AttributeDomain.RELATION_DECLARATION
    if isinstance(
        subject,
        (
            RelationInstanceRef,
            DurableRelationRef,
            PolyadicInstanceRef,
            DurablePolyadicRef,
        ),
    ):
        return AttributeDomain.RELATION_INSTANCE
    if isinstance(subject, DocumentRef):
        return AttributeDomain.DOCUMENT
    if isinstance(subject.was, ItemRef):
        return AttributeDomain.ITEM
    if isinstance(subject.was, BoundaryRef):
        return AttributeDomain.BOUNDARY
    return AttributeDomain.RELATION_INSTANCE


def _domain_article(domain: AttributeDomain) -> str:
    return f"a {domain.value.replace('_', ' ')}"


def _orphan_names(subject: OrphanedSubject) -> Iterator[QualifiedName]:
    """Yield every qualified name one retained coordinate spells.

    An orphan spells at most two: the carrier it stood under, which is a tier
    name unless it is one of the graph's own ordered carriers, and the tier of
    the coordinate it retained, which a bare index does not have.  Both are
    yielded rather than one, because a subject can spell a name in each place
    and the two are independent.
    """
    if isinstance(subject.carrier, QualifiedName):
        yield subject.carrier
    if isinstance(subject.was, ItemRef | BoundaryRef):
        yield subject.was.tier


def _validate_orphaned_subject(layer: Layer, subject: OrphanedSubject) -> None:
    """Hold a retained coordinate to the bound every live coordinate met.

    An orphan is the one layer subject nothing resolves, so this is the only
    place its coordinate is judged.  What it retains is where a live subject
    stood, and a live subject stands at a nonnegative index, so a negative one
    names a position the graph never had.  The declared wire shape states the
    same bound and the reader applies it, which is why this refuses at the
    reader's stage rather than as a graph contract: a graph carrying such a
    coordinate is written out in full and refused on the way back.  The bound
    is spelled here because the declaration module reads this one and cannot be
    read from it; a test binds this spelling to the declared minimum, so the
    two cannot drift apart unnoticed.
    """
    index = subject.was if isinstance(subject.was, int) else subject.was.index
    if index < 0:
        raise GraphValidationError(
            f"layer {layer.name.vocabulary!r}/{layer.name.source!r} holds a fact "
            f"orphaned from {str(subject.was)!r} on {str(subject.carrier)!r}; an "
            "orphan keeps the coordinate its subject stood at, and no subject "
            f"stood at index {index}",
            RefusalStage.VALUE,
        )


def _resolve_layer_subject(graph: Graph, subject: LayerSubject) -> object:
    if isinstance(subject, ItemRef):
        _validate_reference(
            subject, "item reference", graph._tiers_by_name, GraphValidationError
        )
        return subject
    if isinstance(subject, DurableItemRef):
        coordinate = graph._items_by_id.get(subject.durable_id)
        if coordinate is None:
            raise GraphValidationError(
                f"unknown durable item id {subject.durable_id!r}"
            )
        return coordinate
    if isinstance(subject, (BoundaryRef, DurableBoundaryRef)):
        return _resolve_boundary_reference(
            subject,
            graph._tiers_by_name,
            graph._items_by_id,
            GraphValidationError,
        )
    if isinstance(subject, TierRef):
        if subject.tier not in graph._tiers_by_name:
            raise GraphValidationError(
                f"layer subject names undeclared tier {str(subject.tier)!r}"
            )
        return subject.tier
    if isinstance(subject, RelationDeclarationRef):
        if not any(
            item.name == subject.relation for item in graph.relation_declarations
        ):
            raise GraphValidationError(
                f"layer subject names undeclared relation {str(subject.relation)!r}"
            )
        return subject.relation
    if isinstance(subject, RelationInstanceRef):
        if subject.index < 0 or subject.index >= len(graph.relations):
            raise GraphValidationError(
                f"layer subject relation index {subject.index} is outside the graph"
            )
        return subject.index
    if isinstance(subject, PolyadicInstanceRef):
        if subject.index < 0 or subject.index >= len(graph.polyadic_relations):
            raise GraphValidationError(
                f"layer subject polyadic relation index {subject.index} is outside the graph"
            )
        return subject.index
    if isinstance(subject, DurableRelationRef):
        if not any(item.durable_id == subject.durable_id for item in graph.relations):
            raise GraphValidationError(
                f"unknown durable relation id {subject.durable_id!r}"
            )
        return subject.durable_id
    if isinstance(subject, DurablePolyadicRef):
        if not any(
            item.durable_id == subject.durable_id for item in graph.polyadic_relations
        ):
            raise GraphValidationError(
                f"unknown durable polyadic relation id {subject.durable_id!r}"
            )
        return subject.durable_id
    if isinstance(subject, DocumentRef):
        return None
    raise GraphValidationError(  # pragma: no cover - callers exclude orphans
        "an orphaned layer subject has no live coordinate"
    )


def _layer_edit_target(graph: Graph, subject: LayerSubject) -> EditTarget:
    if isinstance(subject, TierRef):
        return subject.tier
    if isinstance(subject, RelationDeclarationRef):
        return subject.relation
    if isinstance(subject, RelationInstanceRef):
        return subject.index
    if isinstance(subject, PolyadicInstanceRef):
        relation = graph.polyadic_relations[subject.index]
        if relation.durable_id is None:
            raise GraphValidationError(
                "a polyadic layer fact cannot be flattened without durable relation identity"
            )
        return relation.durable_id
    if isinstance(subject, DurableRelationRef | DurablePolyadicRef):
        return subject.durable_id
    if isinstance(subject, DocumentRef):
        return None
    if isinstance(subject, OrphanedSubject):  # pragma: no cover - flatten prechecks
        raise GraphValidationError("an orphaned layer subject has no edit target")
    return subject


def _layer_subject_data(subject: LayerSubject) -> dict[str, JsonValue]:
    if isinstance(subject, ItemRef):
        return {
            "kind": "item-coordinate",
            "tier": subject.tier.to_data(),
            "index": subject.index,
        }
    if isinstance(subject, DurableItemRef):
        return {"kind": "durable-item", "durable_id": subject.durable_id}
    if isinstance(subject, BoundaryRef):
        return {
            "kind": "boundary-coordinate",
            "tier": subject.tier.to_data(),
            "index": subject.index,
        }
    if isinstance(subject, DurableBoundaryRef):
        return {"kind": "durable-boundary", **subject.to_data()}
    if isinstance(subject, TierRef):
        return {"kind": "tier", "tier": subject.tier.to_data()}
    if isinstance(subject, RelationDeclarationRef):
        return {"kind": "relation-declaration", "relation": subject.relation.to_data()}
    if isinstance(subject, RelationInstanceRef):
        return {"kind": "relation-instance", "index": subject.index}
    if isinstance(subject, DurableRelationRef):
        return {"kind": "durable-relation", "durable_id": subject.durable_id}
    if isinstance(subject, PolyadicInstanceRef):
        return {"kind": "polyadic-instance", "index": subject.index}
    if isinstance(subject, DurablePolyadicRef):
        return {"kind": "durable-polyadic", "durable_id": subject.durable_id}
    if isinstance(subject, DocumentRef):
        return {"kind": "document"}
    if isinstance(subject.was, ItemRef | BoundaryRef):
        was = _layer_subject_data(subject.was)
    else:
        was = {"kind": "index", "index": subject.was}
    return {"kind": "orphaned", "carrier": _carrier_data(subject.carrier), "was": was}


def _layer_key(key: tuple[LayerSubject, QualifiedName]) -> tuple[str, str, str]:
    subject, name = key
    return (repr(_layer_subject_data(subject)), name.namespace, name.local_name)


def _layer_fact_key(fact: LayerFact) -> tuple[str, str, str]:
    return _layer_key((fact.subject, fact.value.name))


def _remap_layer(layer: Layer, displacement: Displacement) -> Layer:
    facts: list[LayerFact] = []
    for fact in layer.facts:
        subject = fact.subject
        if isinstance(subject, ItemRef):
            subject = (
                OrphanedSubject(subject.tier, subject)
                if subject in displacement.departed_items
                else displacement.items[subject]
            )
        elif isinstance(subject, BoundaryRef):
            subject = (
                OrphanedSubject(subject.tier, subject)
                if subject in displacement.departed_boundaries
                else displacement.boundaries[subject]
            )
        elif isinstance(subject, RelationInstanceRef):
            subject = (
                OrphanedSubject(GraphCarrier.RELATIONS, subject.index)
                if subject.index in displacement.departed_relations
                else RelationInstanceRef(displacement.relations[subject.index])
            )
        elif isinstance(subject, PolyadicInstanceRef):
            subject = (
                OrphanedSubject(GraphCarrier.POLYADIC_RELATIONS, subject.index)
                if subject.index in displacement.departed_polyadic_relations
                else PolyadicInstanceRef(displacement.polyadic_relations[subject.index])
            )
        facts.append(LayerFact(subject, fact.value))
    return Layer(layer.name, tuple(facts))


def _orphan_durable_relation(
    layer: Layer, durable_id: str, carrier: GraphCarrier, index: int
) -> Layer:
    """Retain a durable fact when removal takes away the identity it names."""
    return Layer(
        layer.name,
        tuple(
            LayerFact(
                OrphanedSubject(carrier, index)
                if isinstance(fact.subject, DurableRelationRef | DurablePolyadicRef)
                and fact.subject.durable_id == durable_id
                else fact.subject,
                fact.value,
            )
            for fact in layer.facts
        ),
    )


def _orphan_durable_item(layer: Layer, durable_id: str, coordinate: ItemRef) -> Layer:
    """Retain a durable item fact when removal takes away its identity."""
    return Layer(
        layer.name,
        tuple(
            LayerFact(
                OrphanedSubject(coordinate.tier, coordinate)
                if isinstance(fact.subject, DurableItemRef)
                and fact.subject.durable_id == durable_id
                else fact.subject,
                fact.value,
            )
            for fact in layer.facts
        ),
    )


def _carrier_kind(carrier: SealedCarrier) -> str:
    """Name a carrier's kind for diagnostics."""
    return "tier" if isinstance(carrier, QualifiedName) else "carrier"


def _carrier_count(
    graph: Graph,
    carrier: SealedCarrier,
    tiers: dict[QualifiedName, Tier] | None = None,
) -> int:
    """Return the current extent of one sealable carrier."""
    if isinstance(carrier, QualifiedName):
        known = graph._tiers_by_name if tiers is None else tiers
        tier = known.get(carrier)
        if tier is None:
            raise GraphValidationError(f"seal names undeclared tier {str(carrier)!r}")
        return len(tier.items)
    return len(_carrier_members(graph, carrier))


def _carrier_members(
    graph: Graph, carrier: SealedCarrier
) -> tuple[Item | RelationInstance | PolyadicRelationInstance, ...]:
    """Return one graph carrier in its own order."""
    if isinstance(carrier, QualifiedName):
        tier = next(
            (tier for tier in graph.tiers if tier.declaration.name == carrier), None
        )
        return () if tier is None else tier.items
    if carrier is GraphCarrier.RELATIONS:
        return graph.relations
    return graph.polyadic_relations


def _member_identity(
    member: Item | RelationInstance | PolyadicRelationInstance,
) -> str | None:
    """Return durable geometric identity, or none when values cannot supply it.

    Relation endpoints are coordinates in other carriers. They may shift while
    the relation itself remains at the same coordinate, so they are deliberately
    excluded along with attribute values.
    """
    return member.durable_id


def _seal_breach_detail(
    carrier: SealedCarrier,
    index: int,
    before: Item | RelationInstance | PolyadicRelationInstance,
    after: Item | RelationInstance | PolyadicRelationInstance,
) -> str:
    """Describe the geometric counterexample at one sealed coordinate."""
    coordinate = f"{str(carrier)!r}[{index}]"
    if isinstance(before, Item) and isinstance(after, Item):
        return (
            f"{coordinate} carried durable id {before.durable_id!r} in the source "
            f"and carries {after.durable_id!r} here"
        )
    return f"{coordinate} did not keep its member"


_EMPTY_GRAPH = Graph((), (), ())
