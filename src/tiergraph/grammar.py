"""Synchronous grammar declarations, grammar lowering, and chart recognition."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, cast

from tiergraph.core import (
    Attribute,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    JsonAttributeValue,
    JsonValue,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    TierDeclaration,
    XsdType,
    _canonical_lexical,
    _scalar_attribute,
)
from tiergraph.fold import (
    AttributeValuation,
    ChildCombination,
    FoldCost,
    FoldDeclaration,
    FoldResult,
    FoldTransition,
)
from tiergraph.machine import (
    AddItem,
    AsBuilt,
    AttachValue,
    DeclareAttribute,
    DeclareNamespace,
    DeclareRelation,
    DeclareTier,
    Opcode,
    Program,
    Relate,
)
from tiergraph.machine import (
    _decode_attribute_value as _machine_decode_attribute_value,
)
from tiergraph.machine import _decode_object as _decode_object
from tiergraph.machine import _decode_qname as _machine_decode_qname
from tiergraph.path import (
    AlternativeRef,
    CanonicalPath,
    PathBinding,
    PathOffender,
    PathRefusal,
    PathRefusalCode,
)
from tiergraph.predicate import OffsetProfile, _offset_spans, _validate_offset_profile
from tiergraph.selection import Selector, evaluate_selection
from tiergraph.semiring import BOOLEAN, COUNTING, PATH, PathValue
from tiergraph.wire import _parsed_json

if TYPE_CHECKING:
    from tiergraph.match import DeclaredOrder

GRAMMAR_NAMESPACE = "urn:tiergraph:grammar"
CHART_NAMESPACE = "urn:tiergraph:grammar:chart"
COMPLETE_BOUNDARY = AttributeValue(
    QualifiedName(GRAMMAR_NAMESPACE, "boundary"), XsdType.STRING, "complete"
)
UNIT_WEIGHT = AttributeValue(
    QualifiedName(GRAMMAR_NAMESPACE, "weight"), XsdType.DECIMAL, "1"
)
GENERATION_ENVELOPE = "tiergraph.grammar.generate/1"
TARGET_LATTICE_ENVELOPE = "tiergraph.grammar.target-lattice/1"


def _decode_optional_object(
    value: object, path: str, required: set[str], optional: set[str]
) -> dict[str, object]:
    """Decode an exact object, requiring optional fields to be absent or non-null."""
    present = set(value).intersection(optional) if isinstance(value, dict) else set()
    obj = _decode_object(value, path, required | present)
    null_field = next((name for name in present if obj[name] is None), None)
    if null_field is not None:
        raise ValueError(f"{path}.{null_field} must not be null")
    return obj


def _decode_strings(value: object, path: str) -> tuple[str, ...]:
    """Decode one strict JSON array of strings."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{path} must be an array of strings")
    return tuple(value)


def _decode_item_ref(value: object, path: str) -> ItemRef:
    """Decode one strict structural item reference."""
    obj = _decode_object(value, path, {"tier", "index"})
    index = obj["index"]
    if type(index) is not int:
        raise ValueError(f"{path}.index must be an integer")
    return ItemRef(_decode_qname(obj["tier"], f"{path}.tier"), index)


def _data_fingerprint(value: JsonValue) -> str:
    """Return a SHA-256 digest of canonical strict-JSON data."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class Realization:
    """Carry one experimental target-token alternative for a typed input token."""

    tokens: tuple[str, ...]
    provenance: tuple[str, ...] = ()
    weight: Decimal | None = None

    def __post_init__(self) -> None:
        """Require immutable token and ordered-provenance strings."""
        if type(self.tokens) is not tuple or any(
            not isinstance(token, str) for token in self.tokens
        ):
            raise ValueError("realization tokens must be a tuple of strings")
        if type(self.provenance) is not tuple or any(
            not isinstance(value, str) for value in self.provenance
        ):
            raise ValueError("realization provenance must be a tuple of strings")
        if self.weight is not None and not isinstance(self.weight, Decimal):
            raise ValueError("realization weight must be a Decimal or None")
        if self.weight is not None and not self.weight.is_finite():
            raise ValueError("realization weight must be a finite Decimal or None")

    def to_data(self) -> dict[str, JsonValue]:
        """Return this experimental realization as strict JSON data."""
        data: dict[str, JsonValue] = {"tokens": list(self.tokens)}
        if self.provenance:
            data["provenance"] = list(self.provenance)
        if self.weight is not None:
            data["weight"] = _canonical_lexical(
                XsdType.DECIMAL, format(self.weight, "f")
            )
        return data

    @classmethod
    def from_data(cls, data: object) -> Realization:
        """Decode one strict experimental target realization."""
        path = "grammar input realization"
        obj = _decode_optional_object(data, path, {"tokens"}, {"provenance", "weight"})
        weight = obj.get("weight")
        if weight is not None and not isinstance(weight, str):
            raise ValueError(f"{path}.weight must be a decimal string")
        try:
            decoded_weight = (
                None if weight is None else Decimal(_strict_decimal_lexical(weight))
            )
        except ValueError as error:
            raise ValueError(f"{path}.weight must be a decimal string") from error
        return cls(
            _decode_strings(obj["tokens"], f"{path}.tokens"),
            _decode_strings(obj.get("provenance", []), f"{path}.provenance"),
            decoded_weight,
        )


def _strict_decimal_lexical(lexical: str) -> str:
    """Canonicalize one unpadded xsd:decimal lexical string."""
    if lexical != lexical.strip(" \t\r\n"):
        raise ValueError(lexical)
    return _canonical_lexical(XsdType.DECIMAL, lexical)


@dataclass(frozen=True, slots=True)
class GrammarInputToken:
    """Carry one experimental typed source symbol and its target alternatives."""

    symbol: str
    realization: tuple[Realization, ...]
    provenance: tuple[str, ...] = ()
    source: ItemRef | None = None
    span: SourceSpan | None = None

    def __post_init__(self) -> None:
        """Require a source symbol and at least one immutable realization."""
        if not isinstance(self.symbol, str):
            raise ValueError("grammar input token symbol must be a string")
        if type(self.realization) is not tuple or not self.realization:
            raise ValueError("grammar input token realization must be a nonempty tuple")
        if any(not isinstance(value, Realization) for value in self.realization):
            raise ValueError(
                "grammar input token realization must contain Realization values"
            )
        if type(self.provenance) is not tuple or any(
            not isinstance(value, str) for value in self.provenance
        ):
            raise ValueError(
                "grammar input token provenance must be a tuple of strings"
            )
        if self.source is not None and not isinstance(self.source, ItemRef):
            raise ValueError("grammar input token source must be an ItemRef or None")
        if self.span is not None and not isinstance(self.span, SourceSpan):
            raise ValueError("grammar input token span must be a SourceSpan or None")

    def to_data(self) -> dict[str, JsonValue]:
        """Return this experimental typed input token as strict JSON data."""
        data: dict[str, JsonValue] = {
            "symbol": self.symbol,
            "realization": [value.to_data() for value in self.realization],
        }
        if self.provenance:
            data["provenance"] = list(self.provenance)
        if self.source is not None:
            data["source"] = self.source.to_data()
        if self.span is not None:
            data["span"] = self.span.to_data()
        return data

    @classmethod
    def from_data(cls, data: object) -> GrammarInputToken:
        """Decode one strict experimental typed input token."""
        path = "grammar input token"
        obj = _decode_optional_object(
            data,
            path,
            {"symbol", "realization"},
            {"provenance", "source", "span"},
        )
        symbol = obj["symbol"]
        realizations = obj["realization"]
        if not isinstance(symbol, str):
            raise ValueError(f"{path}.symbol must be a string")
        if not isinstance(realizations, list):
            raise ValueError(f"{path}.realization must be an array")
        return cls(
            symbol,
            tuple(Realization.from_data(value) for value in realizations),
            _decode_strings(obj.get("provenance", []), f"{path}.provenance"),
            None
            if "source" not in obj
            else _decode_item_ref(obj["source"], f"{path}.source"),
            None
            if "span" not in obj
            else SourceSpan.from_data(obj["span"], f"{path}.span"),
        )


@dataclass(frozen=True, slots=True)
class GrammarInput:
    """Hold the experimental typed token sequence retained by a parse forest."""

    tokens: tuple[GrammarInputToken, ...]

    def __post_init__(self) -> None:
        """Require an immutable sequence of typed tokens."""
        if type(self.tokens) is not tuple or any(
            not isinstance(token, GrammarInputToken) for token in self.tokens
        ):
            raise ValueError(
                "grammar input tokens must be a tuple of GrammarInputToken values"
            )

    def to_data(self) -> dict[str, JsonValue]:
        """Return this experimental typed grammar input as strict JSON data."""
        return {"tokens": [token.to_data() for token in self.tokens]}

    @classmethod
    def from_data(cls, data: object) -> GrammarInput:
        """Decode one strict experimental typed grammar input."""
        obj = _decode_object(data, "grammar input", {"tokens"})
        tokens = obj["tokens"]
        if not isinstance(tokens, list):
            raise ValueError("grammar input.tokens must be an array")
        return cls(tuple(GrammarInputToken.from_data(value) for value in tokens))

    @classmethod
    def from_symbols(cls, symbols: Sequence[str]) -> GrammarInput:
        """Wrap raw source symbols with one identity realization apiece."""
        tokens = tuple(symbols)
        if any(not isinstance(token, str) for token in tokens):
            raise ValueError("grammar input token must be a string")
        return cls(
            tuple(
                GrammarInputToken(
                    symbol,
                    (Realization((symbol,)),),
                    span=SourceSpan(None, index, index + 1),
                )
                for index, symbol in enumerate(tokens)
            )
        )

    @classmethod
    def from_graph(
        cls,
        graph: Graph,
        selector: Selector,
        symbol_attribute: QualifiedName,
        realization_attribute: QualifiedName,
        offsets: OffsetProfile,
        *,
        ordering: DeclaredOrder | None = None,
    ) -> GrammarInput:
        """Bind declared graph items to typed grammar tokens with raw offsets."""
        _validate_offset_profile(graph, offsets)
        selected = evaluate_selection(graph, selector)
        nodes = selected.nodes
        if ordering is not None:
            from tiergraph.match import _declared_scope  # noqa: PLC0415

            scope = _declared_scope(graph, ordering)
            if any(node.kind.value != "item" for node in scope.nodes):
                raise ValueError("grammar input ordering must contain only items")
            if set(scope.nodes) != set(selected.nodes):
                raise ValueError(
                    "grammar input ordering members must equal the selected input items"
                )
            nodes = scope.nodes
        spans = _offset_spans(graph, nodes, offsets)
        partitions = {span.partition for span in spans}
        if len(partitions) > 1:
            raise ValueError("grammar input items must belong to one offset partition")
        for previous, current in pairwise(spans):
            if current.origin < previous.origin:
                raise ValueError(
                    "grammar input item origins must be nondecreasing in declared order"
                )
        tokens: list[GrammarInputToken] = []
        for node, offset in zip(nodes, spans, strict=True):
            reference = cast(ItemRef, node.reference)
            attributes = {
                value.name: value for value in _graph_item_attributes(graph, reference)
            }
            symbol = _grammar_input_symbol(
                attributes.get(symbol_attribute), reference, symbol_attribute
            )
            realization = _grammar_input_realizations(
                attributes.get(realization_attribute),
                reference,
                realization_attribute,
            )
            tokens.append(
                GrammarInputToken(
                    symbol,
                    realization,
                    source=reference,
                    span=SourceSpan(offset.partition, offset.origin, offset.end),
                )
            )
        return cls(tuple(tokens))


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Carry one experimental half-open source span."""

    partition: str | None
    origin: int
    end: int

    def __post_init__(self) -> None:
        """Require an optional partition and ordered integer coordinates."""
        if self.partition is not None and not isinstance(self.partition, str):
            raise ValueError("source span partition must be a string or None")
        if type(self.origin) is not int or type(self.end) is not int:
            raise ValueError("source span coordinates must be integers")
        if self.origin > self.end:
            raise ValueError("source span origin must not follow its end")

    def to_data(self) -> dict[str, JsonValue]:
        """Return this experimental half-open source span as strict JSON data."""
        return {
            "partition": self.partition,
            "origin": self.origin,
            "end": self.end,
        }

    @classmethod
    def from_data(cls, data: object, path: str = "source span") -> SourceSpan:
        """Decode one strict experimental half-open source span."""
        obj = _decode_object(data, path, {"partition", "origin", "end"})
        partition = obj["partition"]
        origin = obj["origin"]
        end = obj["end"]
        if partition is not None and not isinstance(partition, str):
            raise ValueError(f"{path}.partition must be a string or null")
        if type(origin) is not int or type(end) is not int:
            raise ValueError(f"{path} coordinates must be integers")
        return cls(partition, origin, end)


def _graph_item_attributes(graph: Graph, reference: ItemRef) -> tuple[Attribute, ...]:
    """Return one resolved graph item's declared attributes."""
    return next(
        tier.items[reference.index].attributes
        for tier in graph.tiers
        if tier.declaration.name == reference.tier
    )


def _grammar_input_symbol(
    value: Attribute | None, reference: ItemRef, name: QualifiedName
) -> str:
    """Read one required scalar string source symbol from a selected item."""
    if value is None:
        raise ValueError(
            f"grammar input item {reference!r} has no symbol attribute {str(name)!r}"
        )
    if not isinstance(value, AttributeValue) or value.value_type is not XsdType.STRING:
        raise ValueError(
            f"grammar input symbol attribute {str(name)!r} must be xsd:string"
        )
    return value.lexical


def _realization_tokens(value: object, subject: str) -> tuple[str, ...]:
    """Validate one nonempty target-token array."""
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(token, str) or not token for token in value)
    ):
        raise ValueError(f"{subject} must be a nonempty array of nonempty strings")
    return tuple(value)


def _grammar_input_realizations(
    value: Attribute | None, reference: ItemRef, name: QualifiedName
) -> tuple[Realization, ...]:
    """Read one or more target-token alternatives from a selected item."""
    subject = f"grammar input realization attribute {str(name)!r}"
    if value is None:
        raise ValueError(
            f"grammar input item {reference!r} has no realization attribute "
            f"{str(name)!r}"
        )
    if isinstance(value, AttributeValue):
        if value.value_type is not XsdType.STRING:
            raise ValueError(f"{subject} must be xsd:string or JSON token arrays")
        tokens = value.lexical.split(" ")
        if not tokens or any(not token for token in tokens):
            raise ValueError(f"{subject} must contain nonempty ASCII-space tokens")
        return (Realization(tuple(tokens)),)
    assert isinstance(value, JsonAttributeValue)
    realized = value.to_value()
    return (Realization(_realization_tokens(realized, subject)),)


@dataclass(frozen=True, slots=True)
class RuleApplication:
    """Carry one experimental generated rule application in witness order."""

    rule_index: int
    provenance: tuple[AttributeValue, ...]
    source_span: SourceSpan
    witness: str

    def to_data(self) -> dict[str, JsonValue]:
        """Return this experimental application record as strict JSON data."""
        return {
            "rule_index": self.rule_index,
            "provenance": [value.to_data() for value in self.provenance],
            "source_span": self.source_span.to_data(),
            "witness": self.witness,
        }

    @classmethod
    def from_data(cls, data: object) -> RuleApplication:
        """Decode one strict experimental application record."""
        path = "generated rule application"
        obj = _decode_object(
            data, path, {"rule_index", "provenance", "source_span", "witness"}
        )
        rule_index = obj["rule_index"]
        provenance = obj["provenance"]
        witness = obj["witness"]
        if type(rule_index) is not int:
            raise ValueError(f"{path}.rule_index must be an integer")
        if not isinstance(provenance, list):
            raise ValueError(f"{path}.provenance must be an array")
        if not isinstance(witness, str):
            raise ValueError(f"{path}.witness must be a string")
        return cls(
            rule_index,
            tuple(
                _decode_attribute_value(value, f"{path}.provenance[{index}]")
                for index, value in enumerate(provenance)
            ),
            SourceSpan.from_data(obj["source_span"], f"{path}.source_span"),
            witness,
        )


@dataclass(frozen=True, slots=True)
class TargetPiece:
    """Carry one experimental emitted token and its introducing provenance."""

    token: str
    witness: str
    application: RuleApplication
    input_provenance: tuple[str, ...] = ()
    source: ItemRef | None = None
    spans: tuple[SourceSpan, ...] = ()

    @property
    def span(self) -> SourceSpan | None:
        """Return the single source interval when the coverage lies in one partition."""
        return self.spans[0] if len(self.spans) == 1 else None

    def to_data(self) -> dict[str, JsonValue]:
        """Return this experimental target piece as strict JSON data."""
        data: dict[str, JsonValue] = {
            "token": self.token,
            "witness": self.witness,
            "application": self.application.to_data(),
            "spans": [span.to_data() for span in self.spans],
        }
        if self.input_provenance:
            data["input_provenance"] = list(self.input_provenance)
        if self.source is not None:
            data["source"] = self.source.to_data()
        if self.span is not None:
            data["span"] = self.span.to_data()
        return data

    @classmethod
    def from_data(cls, data: object) -> TargetPiece:
        """Decode one strict experimental emitted target piece."""
        path = "generated target piece"
        obj = _decode_optional_object(
            data,
            path,
            {"token", "witness", "application", "spans"},
            {"input_provenance", "source", "span"},
        )
        token = obj["token"]
        witness = obj["witness"]
        spans = obj["spans"]
        if not isinstance(token, str) or not isinstance(witness, str):
            raise ValueError(f"{path}.token and .witness must be strings")
        if not isinstance(spans, list):
            raise ValueError(f"{path}.spans must be an array")
        result = cls(
            token,
            witness,
            RuleApplication.from_data(obj["application"]),
            _decode_strings(
                obj.get("input_provenance", []), f"{path}.input_provenance"
            ),
            None
            if "source" not in obj
            else _decode_item_ref(obj["source"], f"{path}.source"),
            tuple(
                SourceSpan.from_data(value, f"{path}.spans[{index}]")
                for index, value in enumerate(spans)
            ),
        )
        if (
            "span" in obj
            and SourceSpan.from_data(obj["span"], f"{path}.span") != result.span
        ):
            raise ValueError(f"{path}.span does not match its coverage set")
        return result


@dataclass(frozen=True, slots=True)
class GeneratedDerivation:
    """Carry one experimental ranked target materialization and exact derivation cost."""

    weight: str
    pieces: tuple[TargetPiece, ...]
    applications: tuple[RuleApplication, ...]
    witness: tuple[str, ...]

    @property
    def tokens(self) -> tuple[str, ...]:
        """Return emitted tokens in declared target order."""
        return tuple(piece.token for piece in self.pieces)

    @property
    def text(self) -> str:
        """Join emitted tokens with the experimental one-ASCII-space profile."""
        return " ".join(self.tokens)

    def to_data(self) -> dict[str, JsonValue]:
        """Return this experimental generated derivation as strict JSON data."""
        return {
            "weight": self.weight,
            "tokens": list(self.tokens),
            "text": self.text,
            "pieces": [piece.to_data() for piece in self.pieces],
            "applications": [item.to_data() for item in self.applications],
            "witness": list(self.witness),
        }

    @classmethod
    def from_data(cls, data: object) -> GeneratedDerivation:
        """Decode one strict experimental generated derivation."""
        path = "generated derivation"
        obj = _decode_object(
            data,
            path,
            {"weight", "tokens", "text", "pieces", "applications", "witness"},
        )
        weight = obj["weight"]
        text = obj["text"]
        pieces = obj["pieces"]
        applications = obj["applications"]
        if not isinstance(weight, str) or not isinstance(text, str):
            raise ValueError(f"{path}.weight and .text must be strings")
        if not isinstance(pieces, list) or not isinstance(applications, list):
            raise ValueError(f"{path}.pieces and .applications must be arrays")
        tokens = _decode_strings(obj["tokens"], f"{path}.tokens")
        result = cls(
            weight,
            tuple(TargetPiece.from_data(value) for value in pieces),
            tuple(RuleApplication.from_data(value) for value in applications),
            _decode_strings(obj["witness"], f"{path}.witness"),
        )
        if result.tokens != tokens or result.text != text:
            raise ValueError(f"{path} text and tokens must match its target pieces")
        return result


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Report experimental bounded target projections and their fold account."""

    derivations: tuple[GeneratedDerivation, ...]
    truncated: bool
    cost: FoldCost

    def to_data(self) -> dict[str, JsonValue]:
        """Return the versioned experimental generation-result envelope."""
        return {
            "experimental": GENERATION_ENVELOPE,
            "derivations": [item.to_data() for item in self.derivations],
            "truncated": self.truncated,
            "cost": cast(dict[str, JsonValue], self.cost.to_data()),
        }

    @classmethod
    def from_data(cls, data: object) -> GenerationResult:
        """Decode one strict versioned experimental generation-result envelope."""
        path = "generation result"
        obj = _decode_object(
            data, path, {"experimental", "derivations", "truncated", "cost"}
        )
        if obj["experimental"] != GENERATION_ENVELOPE:
            raise ValueError("generation result has an unknown experimental version")
        derivations = obj["derivations"]
        truncated = obj["truncated"]
        if not isinstance(derivations, list):
            raise ValueError(f"{path}.derivations must be an array")
        if type(truncated) is not bool:
            raise ValueError(f"{path}.truncated must be a boolean")
        return cls(
            tuple(GeneratedDerivation.from_data(value) for value in derivations),
            truncated,
            _decode_fold_cost(obj["cost"]),
        )


@dataclass(frozen=True, slots=True)
class TargetLattice:
    """View an experimental keep-all target graph without enumerating paths."""

    graph: Graph
    root: ItemRef
    fold: FoldDeclaration[PathValue]
    input: GrammarInput
    declaration: GrammarDeclaration
    cyclic: bool

    def best(self, count: int = 1) -> GenerationResult:
        """Project up to ``count`` ranked targets from the retained graph."""
        if count < 1:
            raise ValueError(
                f"experimental generation count {count!r} must be positive"
            )
        if self.cyclic:
            raise ValueError("target materialization requires a finite derivation")
        result = replace(self.fold, output_cap=count).run()
        ranked = cast(
            tuple[tuple[PathValue, tuple[str, ...]], ...], result.ranked_witnesses
        )
        derivations = tuple(
            _generated_derivation(self, value, witness) for value, witness in ranked
        )
        return GenerationResult(derivations, result.truncated, result.cost)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the versioned experimental keep-all lattice envelope."""
        return {
            "experimental": TARGET_LATTICE_ENVELOPE,
            "graph": self.graph.to_data(),
            "root": self.root.to_data(),
            "generation": _generation_declaration_data(self.fold),
            "input_fingerprint": _data_fingerprint(self.input.to_data()),
            "cyclic": self.cyclic,
        }


def _decode_fold_cost(data: object) -> FoldCost:
    """Decode and verify the strict public cost account in a generation result."""
    path = "generation result.cost"
    stored = (
        "document_size",
        "relation_incidence",
        "index_product_size",
        "carrier_additions",
        "carrier_multiplications",
        "carrier_operation_cost",
        "witness_count",
        "emitted_count",
        "output_cap",
    )
    derived = ("carrier_work", "bound", "measured_work")
    obj = _decode_optional_object(
        data,
        path,
        set(stored + derived),
        {"witness_operations", "ranked_multiplications"},
    )
    if any(type(value) is not int for value in obj.values()):
        raise ValueError(f"{path} fields must be integers")
    result = FoldCost(
        document_size=cast(int, obj["document_size"]),
        relation_incidence=cast(int, obj["relation_incidence"]),
        index_product_size=cast(int, obj["index_product_size"]),
        carrier_additions=cast(int, obj["carrier_additions"]),
        carrier_multiplications=cast(int, obj["carrier_multiplications"]),
        carrier_operation_cost=cast(int, obj["carrier_operation_cost"]),
        witness_count=cast(int, obj["witness_count"]),
        emitted_count=cast(int, obj["emitted_count"]),
        output_cap=cast(int, obj["output_cap"]),
        witness_operations=cast(int, obj.get("witness_operations", 0)),
        ranked_multiplications=cast(int, obj.get("ranked_multiplications", 0)),
    )
    for name in derived:
        if obj[name] != getattr(result, name):
            raise ValueError(f"{path}.{name} does not match the measured account")
    return result


def _generation_declaration_data(
    fold: FoldDeclaration[PathValue],
) -> dict[str, JsonValue]:
    """Return the data-only part of the target fold declaration."""
    if fold.witness_order is not None:
        raise ValueError("target lattice cannot encode a custom witness order")
    return {
        "name": fold.name,
        "valuation": {
            "name": fold.valuation.name,
            "attribute": fold.valuation.attribute.to_data(),
            "tiers": [tier.to_data() for tier in fold.valuation.tiers],
        },
        "semiring": "path",
        "transitions": [
            {
                "relation": transition.relation.to_data(),
                "combination": transition.combination.value,
            }
            for transition in fold.transitions
        ],
        "index_axes": [list(axis) for axis in fold.index_axes],
        "roots": [root.to_data() for root in fold.roots],
        "witness_order": None,
        "tie_policy": None if fold.tie_policy is None else fold.tie_policy.value,
        "output_cap": fold.output_cap,
        "ranked_output": fold.ranked_output,
        "exactness": fold.exactness.value,
    }


def _decode_qname(value: object, path: str) -> QualifiedName:
    obj = _decode_object(value, path, {"namespace", "local_name"})
    if not isinstance(obj["namespace"], str) or not isinstance(obj["local_name"], str):
        raise ValueError(f"{path} has invalid field types")
    return _machine_decode_qname(value, path)


def _decode_attribute_value(value: object, path: str) -> AttributeValue:
    obj = _decode_object(value, path, {"name", "value_type", "lexical"})
    name = _decode_object(obj["name"], f"{path}.name", {"namespace", "local_name"})
    if (
        not isinstance(name["namespace"], str)
        or not isinstance(name["local_name"], str)
        or not isinstance(obj["value_type"], str)
        or not isinstance(obj["lexical"], str)
    ):
        raise ValueError(f"{path} has invalid field types")
    return _scalar_attribute(_machine_decode_attribute_value(value, path))


def _string_value(value: AttributeValue, subject: str) -> None:
    value = _scalar_attribute(value)
    if value.value_type is not XsdType.STRING:
        raise ValueError(
            f"{subject} {value.lexical!r} must be carried as an xsd:string value"
        )


@dataclass(frozen=True, slots=True)
class GrammarTerminal:
    """Carry one source or target terminal as a canonical XSD string value."""

    text: AttributeValue

    def __post_init__(self) -> None:
        """Refuse a terminal whose declared value is not an XSD string."""
        _string_value(self.text, "terminal text")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the terminal declaration as JSON-serializable data."""
        return {"kind": "terminal", "text": self.text.to_data()}

    @classmethod
    def from_data(cls, data: object) -> GrammarTerminal:
        """Decode one strict terminal declaration from JSON-compatible data."""
        obj = _decode_object(data, "grammar terminal", {"kind", "text"})
        if obj["kind"] != "terminal":
            raise ValueError(
                f"grammar terminal.kind {obj['kind']!r} must be 'terminal'"
            )
        return cls(_decode_attribute_value(obj["text"], "grammar terminal.text"))


@dataclass(frozen=True, slots=True)
class GrammarHole:
    """Bind one named pattern variable to a declared nonterminal."""

    variable: AttributeValue
    nonterminal: QualifiedName

    def __post_init__(self) -> None:
        """Refuse a variable whose declared value is not an XSD string."""
        _string_value(self.variable, "hole variable")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the hole declaration as JSON-serializable data."""
        return {
            "kind": "hole",
            "variable": self.variable.to_data(),
            "nonterminal": self.nonterminal.to_data(),
        }

    @classmethod
    def from_data(cls, data: object) -> GrammarHole:
        """Decode one strict hole declaration from JSON-compatible data."""
        obj = _decode_object(data, "grammar hole", {"kind", "variable", "nonterminal"})
        if obj["kind"] != "hole":
            raise ValueError(f"grammar hole.kind {obj['kind']!r} must be 'hole'")
        return cls(
            _decode_attribute_value(obj["variable"], "grammar hole.variable"),
            _decode_qname(obj["nonterminal"], "grammar hole.nonterminal"),
        )


type GrammarPatternElement = GrammarTerminal | GrammarHole
type GrammarPattern = tuple[GrammarPatternElement, ...]


def _decode_pattern(data: object, path: str) -> GrammarPattern:
    if not isinstance(data, list):
        raise ValueError(f"{path} must be an array")
    elements: list[GrammarPatternElement] = []
    for index, value in enumerate(data):
        element_path = f"{path}[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{element_path} must be an object")
        kind = value.get("kind")
        if kind == "terminal":
            elements.append(GrammarTerminal.from_data(value))
        elif kind == "hole":
            elements.append(GrammarHole.from_data(value))
        else:
            raise ValueError(f"{element_path}.kind {kind!r} is unknown")
    return tuple(elements)


@dataclass(frozen=True, slots=True)
class GrammarRule:
    """Declare one directional pairing of source and target patterns."""

    left: QualifiedName
    source: GrammarPattern
    target: GrammarPattern
    boundary: AttributeValue = COMPLETE_BOUNDARY
    awaited_variables: tuple[AttributeValue, ...] = ()
    weight: AttributeValue | None = None
    provenance: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize set-like awaited variables and validate XSD carriers."""
        _string_value(self.boundary, f"rule {str(self.left)!r} boundary")
        for variable in self.awaited_variables:
            _string_value(variable, f"rule {str(self.left)!r} awaited variable")
        if self.weight is not None:
            _scalar_attribute(self.weight)
        if self.weight is not None and self.weight.value_type is not XsdType.DECIMAL:
            raise ValueError(
                f"rule {str(self.left)!r} weight {self.weight.lexical!r} "
                "must be carried as an xsd:decimal value"
            )
        if type(self.provenance) is not tuple:
            raise ValueError(f"rule {str(self.left)!r} provenance must be a tuple")
        for value in self.provenance:
            if not isinstance(value, AttributeValue):
                raise ValueError(
                    f"rule {str(self.left)!r} provenance must contain "
                    "AttributeValue values"
                )
            _string_value(value, f"rule {str(self.left)!r} provenance")
        provenance_names = [value.name for value in self.provenance]
        if len(set(provenance_names)) != len(provenance_names):
            raise ValueError(f"rule {str(self.left)!r} has duplicate provenance names")
        ordered = tuple(sorted(self.awaited_variables, key=lambda value: value.lexical))
        if len({value.lexical for value in ordered}) != len(ordered):
            raise ValueError(f"rule {str(self.left)!r} has duplicate awaited variables")
        object.__setattr__(self, "awaited_variables", ordered)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the directional rule as JSON-serializable data."""
        data: dict[str, JsonValue] = {
            "left": self.left.to_data(),
            "source": [element.to_data() for element in self.source],
            "target": [element.to_data() for element in self.target],
            "boundary": self.boundary.to_data(),
            "awaited_variables": [
                variable.to_data() for variable in self.awaited_variables
            ],
            "weight": None if self.weight is None else self.weight.to_data(),
        }
        if self.provenance:
            data["provenance"] = [value.to_data() for value in self.provenance]
        return data

    @classmethod
    def from_data(cls, data: object) -> GrammarRule:
        """Decode one strict directional rule from JSON-compatible data."""
        path = "grammar rule"
        required = {
            "left",
            "source",
            "target",
            "boundary",
            "awaited_variables",
            "weight",
        }
        obj = _decode_object(
            data,
            path,
            required
            | (
                {"provenance"}
                if isinstance(data, dict) and "provenance" in data
                else set()
            ),
        )
        awaited = obj["awaited_variables"]
        if not isinstance(awaited, list):
            raise ValueError(f"{path}.awaited_variables must be an array")
        weight = obj["weight"]
        if weight is not None and not isinstance(weight, dict):
            raise ValueError(f"{path}.weight must be an attribute value or null")
        provenance = obj.get("provenance", [])
        if not isinstance(provenance, list):
            raise ValueError(f"{path}.provenance must be an array")
        return cls(
            _decode_qname(obj["left"], f"{path}.left"),
            _decode_pattern(obj["source"], f"{path}.source"),
            _decode_pattern(obj["target"], f"{path}.target"),
            _decode_attribute_value(obj["boundary"], f"{path}.boundary"),
            tuple(
                _decode_attribute_value(value, f"{path}.awaited_variables[{index}]")
                for index, value in enumerate(awaited)
            ),
            None
            if weight is None
            else _decode_attribute_value(weight, f"{path}.weight"),
            tuple(
                _decode_attribute_value(value, f"{path}.provenance[{index}]")
                for index, value in enumerate(provenance)
            ),
        )

    @property
    def effective_weight(self) -> AttributeValue:
        """Return the declared weight or the unit rule cost."""
        return UNIT_WEIGHT if self.weight is None else self.weight


@dataclass(frozen=True, slots=True)
class GrammarDeclaration:
    """Hold a validated synchronous grammar with fixed source and target roles."""

    nonterminals: tuple[QualifiedName, ...]
    start: QualifiedName
    rules: tuple[GrammarRule, ...]

    def __post_init__(self) -> None:
        """Refuse undeclared symbols and inconsistent rule-variable sets."""
        if len(set(self.nonterminals)) != len(self.nonterminals):
            raise ValueError("grammar has duplicate nonterminal declarations")
        declared = set(self.nonterminals)
        if self.start not in declared:
            raise ValueError(
                f"grammar start {str(self.start)!r} is not a declared nonterminal"
            )
        for index, rule in enumerate(self.rules):
            label = f"rule {index} ({str(rule.left)!r})"
            if rule.left not in declared:
                raise ValueError(f"{label} left-hand nonterminal is not declared")
            for role, pattern in (("source", rule.source), ("target", rule.target)):
                if type(pattern) is not tuple:
                    raise ValueError(
                        f"{label} {role} pattern {pattern!r} must be a tuple"
                    )
                for element_index, element in enumerate(pattern):
                    if not isinstance(element, GrammarTerminal | GrammarHole):
                        raise ValueError(
                            f"{label} {role} element {element_index} {element!r} "
                            "must be a GrammarTerminal or GrammarHole"
                        )
                    if (
                        isinstance(element, GrammarHole)
                        and element.nonterminal not in declared
                    ):
                        raise ValueError(
                            f"{label} {role} hole {element_index} nonterminal "
                            f"{str(element.nonterminal)!r} is not declared"
                        )
            source = {
                element.variable.lexical
                for element in rule.source
                if isinstance(element, GrammarHole)
            }
            target = {
                element.variable.lexical
                for element in rule.target
                if isinstance(element, GrammarHole)
            }
            awaited = {value.lexical for value in rule.awaited_variables}
            overlap = source.intersection(awaited)
            if overlap:
                raise ValueError(
                    f"{label} variables {sorted(overlap)!r} are both source-bound and awaited"
                )
            expected = source.union(awaited)
            if target != expected:
                raise ValueError(
                    f"{label} target variables {sorted(target)!r} must equal source "
                    f"variables plus awaited variables {sorted(expected)!r}"
                )

    def to_data(self) -> dict[str, JsonValue]:
        """Return the grammar declaration as JSON-serializable data."""
        return {
            "nonterminals": [name.to_data() for name in self.nonterminals],
            "start": self.start.to_data(),
            "rules": [rule.to_data() for rule in self.rules],
        }

    @classmethod
    def from_data(cls, data: object) -> GrammarDeclaration:
        """Decode one strict grammar declaration from JSON-compatible data."""
        obj = _decode_object(data, "grammar", {"nonterminals", "start", "rules"})
        nonterminals = obj["nonterminals"]
        rules = obj["rules"]
        if not isinstance(nonterminals, list):
            raise ValueError("grammar.nonterminals must be an array")
        if not isinstance(rules, list):
            raise ValueError("grammar.rules must be an array")
        return cls(
            tuple(
                _decode_qname(value, f"grammar.nonterminals[{index}]")
                for index, value in enumerate(nonterminals)
            ),
            _decode_qname(obj["start"], "grammar.start"),
            tuple(GrammarRule.from_data(value) for value in rules),
        )


def grammar_loads(source: str | bytes) -> GrammarDeclaration:
    """Decode a strict grammar declaration from UTF-8 JSON text or bytes.

    The text is read under the same envelope, encoding, and syntax stages the
    graph document reader applies, so a caller routes on a declared stage here
    as well as there.
    """
    return GrammarDeclaration.from_data(_parsed_json(source))


@dataclass(frozen=True, slots=True)
class LoweredGrammar:
    """Pair a grammar with its replayable ordered-hedge construction."""

    declaration: GrammarDeclaration
    program: Program
    as_built: AsBuilt

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration, graph, and construction fingerprint."""
        return {
            "declaration": self.declaration.to_data(),
            "as_built": self.as_built.to_data(),
            "fingerprint": self.program.fingerprint(),
        }


def _name(namespace: str, local: str) -> QualifiedName:
    return QualifiedName(namespace, local)


def _provenance_names(declaration: GrammarDeclaration) -> tuple[QualifiedName, ...]:
    """Return each declared rule-provenance name once in canonical order."""
    return tuple(
        sorted(
            {value.name for rule in declaration.rules for value in rule.provenance},
            key=str,
        )
    )


def _provenance_namespace_opcodes(
    declaration: GrammarDeclaration, namespace: str
) -> tuple[DeclareNamespace, ...]:
    """Declare external provenance namespaces with deterministic local prefixes."""
    namespaces = sorted(
        {name.namespace for name in _provenance_names(declaration)} - {namespace}
    )
    return tuple(
        DeclareNamespace(NamespaceDeclaration(f"provenance{index}", value))
        for index, value in enumerate(namespaces)
    )


def _check_provenance_name_conflicts(
    declaration: GrammarDeclaration,
    reserved: set[QualifiedName],
    subject: str,
) -> None:
    """Refuse a client provenance name already owned by the generated graph."""
    conflict = next(
        (name for name in _provenance_names(declaration) if name in reserved), None
    )
    if conflict is not None:
        raise ValueError(
            f"rule provenance name {str(conflict)!r} conflicts with {subject} attribute"
        )


def lower_grammar(
    declaration: GrammarDeclaration, namespace: str = GRAMMAR_NAMESPACE
) -> LoweredGrammar:
    """Lower a grammar through machine opcodes to an ordered hedge."""
    names = {
        local: _name(namespace, local)
        for local in (
            "productions",
            "slots",
            "elements",
            "production-slots",
            "slot-elements",
            "kind",
            "nonterminal",
            "boundary",
            "text",
            "variable",
            "weight",
        )
    }
    _check_provenance_name_conflicts(
        declaration, set(names.values()), "grammar lowering"
    )
    opcodes: list[Opcode] = [
        DeclareNamespace(NamespaceDeclaration("grammar", namespace)),
        *_provenance_namespace_opcodes(declaration, namespace),
    ]
    for local, long_name in (
        ("productions", "Grammar productions"),
        ("slots", "Grammar pattern slots"),
        ("elements", "Grammar pattern elements"),
    ):
        opcodes.append(DeclareTier(TierDeclaration(names[local], long_name)))
    item_side = (RelationEndpointKind.ITEM,)
    opcodes.extend(
        (
            DeclareRelation(
                PolyadicRelationDeclaration(
                    names["production-slots"],
                    RelationSideDeclaration(item_side, (names["productions"],), 1, 1),
                    RelationSideDeclaration(item_side, (names["slots"],), 2, 2),
                    unique_sources=True,
                )
            ),
            DeclareRelation(
                PolyadicRelationDeclaration(
                    names["slot-elements"],
                    RelationSideDeclaration(item_side, (names["slots"],), 1, 1),
                    RelationSideDeclaration(
                        item_side, (names["elements"],), 0, None, True
                    ),
                    unique_sources=True,
                    distinct_targets=True,
                    single_parent=True,
                )
            ),
        )
    )
    opcodes.extend(
        DeclareAttribute(
            AttributeDeclaration(names[local], AttributeDomain.ITEM, XsdType.STRING)
        )
        for local in ("kind", "nonterminal", "boundary", "text", "variable")
    )
    opcodes.append(
        DeclareAttribute(
            AttributeDeclaration(names["weight"], AttributeDomain.ITEM, XsdType.DECIMAL)
        )
    )
    opcodes.extend(
        DeclareAttribute(
            AttributeDeclaration(name, AttributeDomain.ITEM, XsdType.STRING)
        )
        for name in _provenance_names(declaration)
    )
    slot_index = 0
    element_index = 0
    for rule_index, rule in enumerate(declaration.rules):
        production = ItemRef(names["productions"], rule_index)
        opcodes.append(AddItem(names["productions"], Item()))
        for local, lexical in (
            ("kind", "grammar-rule-rhs"),
            ("nonterminal", str(rule.left)),
            ("boundary", rule.boundary.lexical),
        ):
            opcodes.append(
                AttachValue(
                    AttributeDomain.ITEM,
                    production,
                    AttributeValue(names[local], XsdType.STRING, lexical),
                )
            )
        opcodes.append(
            AttachValue(
                AttributeDomain.ITEM,
                production,
                AttributeValue(
                    names["weight"], XsdType.DECIMAL, rule.effective_weight.lexical
                ),
            )
        )
        opcodes.extend(
            AttachValue(AttributeDomain.ITEM, production, value)
            for value in rule.provenance
        )
        slots: list[ItemRef] = []
        for role, pattern in (("source", rule.source), ("target", rule.target)):
            slot = ItemRef(names["slots"], slot_index)
            slot_index += 1
            slots.append(slot)
            opcodes.append(AddItem(names["slots"], Item()))
            opcodes.append(
                AttachValue(
                    AttributeDomain.ITEM,
                    slot,
                    AttributeValue(names["kind"], XsdType.STRING, role),
                )
            )
            elements: list[ItemRef] = []
            for element in pattern:
                reference = ItemRef(names["elements"], element_index)
                element_index += 1
                elements.append(reference)
                opcodes.append(AddItem(names["elements"], Item()))
                values: tuple[tuple[str, str], ...]
                if isinstance(element, GrammarTerminal):
                    values = (("kind", "terminal"), ("text", element.text.lexical))
                else:
                    values = (
                        ("kind", "hole"),
                        ("variable", element.variable.lexical),
                        ("nonterminal", str(element.nonterminal)),
                    )
                for local, lexical in values:
                    opcodes.append(
                        AttachValue(
                            AttributeDomain.ITEM,
                            reference,
                            AttributeValue(names[local], XsdType.STRING, lexical),
                        )
                    )
            opcodes.append(
                Relate(
                    PolyadicRelationInstance(
                        names["slot-elements"], (slot,), tuple(elements)
                    )
                )
            )
        opcodes.append(
            Relate(
                PolyadicRelationInstance(
                    names["production-slots"], (production,), tuple(slots)
                )
            )
        )
    program = Program(tuple(opcodes))
    return LoweredGrammar(declaration, program, program.unroll())


@dataclass(frozen=True, slots=True)
class ParseForest:
    """Carry a machine-built parse forest and its Boolean interpretation."""

    graph: Graph
    program: Program
    root: ItemRef
    fold: FoldDeclaration[bool]
    declaration: GrammarDeclaration
    collapsed: bool = True
    input: GrammarInput | None = None

    def recognized(self) -> bool:
        """Return whether the designated start span has a derivation."""
        return self.fold.run().value

    def result(self) -> FoldResult[bool]:
        """Evaluate and return the complete Boolean fold result."""
        return self.fold.run()

    def count(self) -> int:
        """Count derivations when the grammar lies in the finite-fold domain."""
        if self.collapsed:
            raise ValueError(
                "count requires a parse forest built with collapse_units=False"
            )
        return _count_fold(self).run().value

    def best(self, count: int = 1) -> tuple[BestDerivation, ...]:
        """Return up to ``count`` cheapest derivations, by exact total cost.

        The grammar must lie in the finite-fold domain. Costs are exact and the
        returned order is nondecreasing by cost. Among derivations of equal cost a
        deterministic subset is returned; that tie selection is not guaranteed to be a
        globally canonical one, because ranking keeps the cheapest by cost rather than
        by witness identity.
        """
        if self.collapsed:
            raise ValueError(
                "best requires a parse forest built with collapse_units=False"
            )
        return _best_derivations(self, count)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the forest, root, fingerprint, and Boolean answer as JSON data."""
        return {
            "graph": self.graph.to_data(),
            "root": self.root.to_data(),
            "fingerprint": self.program.fingerprint(),
            "recognized": self.recognized(),
        }


@dataclass(frozen=True, slots=True)
class _GrammarChartSnapshot:
    """Hold the graph identity and root a chart path profile actually reads."""

    graph: Graph
    root: ItemRef


@dataclass(frozen=True, slots=True)
class GrammarChartProfile:
    """Address chart alternatives in a stable order within one forest snapshot.

    The profile vocabulary is
    ``/chart/NONTERMINAL/START/END/alternatives/INDEX``. Alternative indices are
    independent of rule weights, but intentionally are not stable across forest
    snapshots whose sets of alternatives differ.
    """

    forest: ParseForest

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declarative chart-profile input used by the CLI."""
        return {"kind": "grammar-chart", "root": self.forest.root.to_data()}

    @classmethod
    def from_data(cls, graph: Graph, data: object) -> GrammarChartProfile:
        """Bind a declarative chart profile to one graph snapshot."""
        obj = _decode_object(data, "grammar chart profile", {"kind", "root"})
        if obj["kind"] != "grammar-chart":
            raise ValueError("grammar chart profile.kind must be 'grammar-chart'")
        root = _decode_item_ref(obj["root"], "grammar chart profile.root")
        graph.resolve_item(root)
        return cls(cast(ParseForest, _GrammarChartSnapshot(graph, root)))

    def bind(self, path: CanonicalPath, graph: Graph) -> PathBinding:
        """Bind a chart coordinate and profile-owned alternatives literal."""
        if graph is not self.forest.graph:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text=str(path),
                    path=path,
                    profile_reason="different_forest_snapshot",
                ),
            )
        segments = path.segments
        chart_path_segment_count = 6
        if (
            len(segments) != chart_path_segment_count
            or segments[0] != "chart"
            or segments[4] != "alternatives"
        ):
            raise PathRefusal(
                PathRefusalCode.UNKNOWN_FORM,
                PathOffender(text=str(path), path=path),
            )
        start = _chart_path_index(segments[2], 2, path)
        end = _chart_path_index(segments[3], 3, path)
        index = _chart_path_index(segments[5], 5, path)
        names = _forest_names(self.forest)
        owner = next(
            (
                reference
                for reference in graph.canonical_items()
                if reference.tier == names["chart-items"]
                and _item_attribute(graph, reference, "nonterminal").lexical
                == segments[1]
                and _item_attribute(graph, reference, "start").lexical == str(start)
                and _item_attribute(graph, reference, "end").lexical == str(end)
            ),
            None,
        )
        if owner is None:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text=str(path), path=path, profile_reason="unknown_chart_item"
                ),
            )
        return AlternativeRef(owner, names["alternatives"], index)

    def spell(self, binding: PathBinding, graph: Graph) -> CanonicalPath:
        """Spell an alternative binding in this chart vocabulary."""
        if not isinstance(binding, AlternativeRef) or graph is not self.forest.graph:
            raise PathRefusal(
                PathRefusalCode.UNSPELLABLE,
                PathOffender(text="", profile_reason="unsupported_binding"),
            )
        names = _forest_names(self.forest)
        if binding.relation != names["alternatives"]:
            raise PathRefusal(
                PathRefusalCode.UNSPELLABLE,
                PathOffender(text="", profile_reason="unsupported_relation"),
            )
        owner = graph.resolve_item(binding.owner)
        if owner.tier != names["chart-items"]:
            raise PathRefusal(
                PathRefusalCode.UNSPELLABLE,
                PathOffender(text="", profile_reason="unsupported_owner"),
            )
        return CanonicalPath(
            (
                "chart",
                _item_attribute(graph, owner, "nonterminal").lexical,
                _item_attribute(graph, owner, "start").lexical,
                _item_attribute(graph, owner, "end").lexical,
                "alternatives",
                str(binding.index),
            )
        )

    def alternatives(
        self, owner: ItemRef, relation: QualifiedName, graph: Graph
    ) -> tuple[object, ...]:
        """Order by application start, ordered child spans, then application index."""
        names = _forest_names(self.forest)
        if graph is not self.forest.graph or relation != names["alternatives"]:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text="", relation=relation, profile_reason="unsupported_relation"
                ),
            )
        if owner.tier != names["chart-items"]:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text="", tier=owner.tier, profile_reason="unsupported_owner"
                ),
            )
        applications = tuple(
            cast(ItemRef, edge.right)
            for edge in graph.relations
            if edge.declaration == relation and edge.left == owner
        )

        def _stable_key(
            application: ItemRef,
        ) -> tuple[int, tuple[tuple[int, int], ...], int]:
            production = next(
                instance
                for instance in graph.polyadic_relations
                if instance.declaration == names["production-application"]
                and instance.sources == (application,)
            )
            spans = tuple(
                (
                    int(_item_attribute(graph, cast(ItemRef, child), "start").lexical),
                    int(_item_attribute(graph, cast(ItemRef, child), "end").lexical),
                )
                for child in production.targets
            )
            # (application start, child spans) is the semantic order; the application's
            # own tier index is a canonical, unique tiebreak so genuinely colliding
            # variants (e.g. two nullable expansions that project to the identical
            # (production, children)) get a stable order rather than falling back
            # to graph.relations incidence order. The tier index is builder-assigned
            # and survives any reordering of relation instances.
            return (
                int(_item_attribute(graph, application, "start").lexical),
                spans,
                application.index,
            )

        return tuple(sorted(applications, key=_stable_key))


@dataclass(frozen=True, slots=True)
class BestDerivation:
    """Carry an exact total cost and one deterministic derivation witness."""

    weight: str
    witness: tuple[str, ...]

    def to_data(self) -> dict[str, JsonValue]:
        """Return the result as JSON-serializable data."""
        return {"weight": self.weight, "witness": list(self.witness)}


def _candidate_matches(
    pattern: GrammarPattern,
    tokens: tuple[str, ...],
    start: int,
    end: int,
    *,
    allow_empty_holes: bool = False,
) -> tuple[tuple[tuple[tuple[QualifiedName, int, int], ...], bool], ...]:
    found: list[tuple[tuple[tuple[QualifiedName, int, int], ...], bool]] = []

    def _visit(
        index: int,
        cursor: int,
        children: tuple[tuple[QualifiedName, int, int], ...],
        terminals_match: bool,
    ) -> None:
        if index == len(pattern):
            if cursor == end:
                found.append((children, terminals_match))
            return
        element = pattern[index]
        if isinstance(element, GrammarTerminal):
            if cursor < end:
                _visit(
                    index + 1,
                    cursor + 1,
                    children,
                    terminals_match and tokens[cursor] == element.text.lexical,
                )
            return
        first = cursor if allow_empty_holes else cursor + 1
        for boundary in range(first, end + 1):
            key = (element.nonterminal, cursor, boundary)
            _visit(index + 1, boundary, (*children, key), terminals_match)

    _visit(0, start, (), True)
    return tuple(found)


def _recognition_rules(
    declaration: GrammarDeclaration,
    source_rules: tuple[tuple[QualifiedName, GrammarPattern], ...],
) -> tuple[tuple[int, QualifiedName, GrammarPattern], ...]:
    nullable: set[QualifiedName] = set()
    changed = True
    while changed:
        changed = False
        for left, pattern in source_rules:
            if left in nullable:
                continue
            if all(
                isinstance(element, GrammarHole) and element.nonterminal in nullable
                for element in pattern
            ):
                nullable.add(left)
                changed = True

    expanded: list[tuple[int, QualifiedName, GrammarPattern]] = []
    for rule_index, (left, pattern) in enumerate(source_rules):
        variants: list[GrammarPattern] = [()]
        for element in pattern:
            kept = [(*variant, element) for variant in variants]
            if isinstance(element, GrammarHole) and element.nonterminal in nullable:
                variants = [*variants, *kept]
            else:
                variants = kept
        for variant in variants:
            candidate = (rule_index, left, variant)
            if candidate not in expanded:
                expanded.append(candidate)

    unit_targets: dict[QualifiedName, tuple[QualifiedName, ...]] = {}
    for left in declaration.nonterminals:
        targets = tuple(
            pattern[0].nonterminal
            for _, candidate_left, pattern in expanded
            if candidate_left == left
            and len(pattern) == 1
            and isinstance(pattern[0], GrammarHole)
        )
        unit_targets[left] = tuple(dict.fromkeys(targets))

    result: list[tuple[int, QualifiedName, GrammarPattern]] = []
    for left in declaration.nonterminals:
        closure = [left]
        for reachable_unit in closure:
            for target in unit_targets[reachable_unit]:
                if target not in closure:
                    closure.append(target)
        for reachable in closure:
            for rule_index, candidate_left, pattern in expanded:
                unit = len(pattern) == 1 and isinstance(pattern[0], GrammarHole)
                candidate = (rule_index, left, pattern)
                if candidate_left == reachable and not unit and candidate not in result:
                    result.append(candidate)
    return tuple(result)


def _source_rules(
    grammar: LoweredGrammar,
) -> tuple[tuple[QualifiedName, GrammarPattern], ...]:
    graph = grammar.as_built.graph
    tiers = {tier.declaration.name.local_name: tier for tier in graph.tiers}
    productions = tiers["productions"]
    elements = tiers["elements"]
    slots_by_production = {
        cast(ItemRef, relation.sources[0]): tuple(
            cast(ItemRef, target) for target in relation.targets
        )
        for relation in graph.polyadic_relations
        if relation.declaration.local_name == "production-slots"
    }
    elements_by_slot = {
        cast(ItemRef, relation.sources[0]): tuple(
            cast(ItemRef, target) for target in relation.targets
        )
        for relation in graph.polyadic_relations
        if relation.declaration.local_name == "slot-elements"
    }
    names = {str(name): name for name in grammar.declaration.nonterminals}
    rules: list[tuple[QualifiedName, GrammarPattern]] = []
    for index, production in enumerate(productions.items):
        values = {
            value.name.local_name: _scalar_attribute(value).lexical
            for value in production.attributes
        }
        left = names[values["nonterminal"]]
        source_slot = slots_by_production[ItemRef(productions.declaration.name, index)][
            0
        ]
        pattern: list[GrammarPatternElement] = []
        for reference in elements_by_slot[source_slot]:
            element_values = {
                value.name.local_name: _scalar_attribute(value)
                for value in elements.items[reference.index].attributes
            }
            if element_values["kind"].lexical == "terminal":
                pattern.append(GrammarTerminal(element_values["text"]))
            else:
                pattern.append(
                    GrammarHole(
                        element_values["variable"],
                        names[element_values["nonterminal"].lexical],
                    )
                )
        rules.append((left, tuple(pattern)))
    return tuple(rules)


def recognize(
    grammar: LoweredGrammar,
    input_tokens: Sequence[str] | GrammarInput,
    namespace: str = CHART_NAMESPACE,
    *,
    collapse_units: bool = True,
) -> ParseForest:
    """Build a chart forest for token input using polynomial span deduction.

    For a fixed grammar whose longest source pattern has length ``m``, the
    exhaustive boundary discipline takes ``O(n^(m+1))`` time and polynomial
    space in input length ``n``.
    """
    grammar_input = (
        input_tokens
        if isinstance(input_tokens, GrammarInput)
        else GrammarInput.from_symbols(input_tokens)
    )
    tokens = tuple(token.symbol for token in grammar_input.tokens)
    declaration = grammar.declaration
    source_rules = _source_rules(grammar)
    recognition_rules = (
        _recognition_rules(declaration, source_rules)
        if collapse_units
        else tuple(
            (rule_index, left, pattern)
            for rule_index, (left, pattern) in enumerate(source_rules)
        )
    )
    keys, applications = _deduce_chart(
        declaration, recognition_rules, tokens, collapse_units=collapse_units
    )
    root_key = (declaration.start, 0, len(tokens))
    references = {
        key: ItemRef(_name(namespace, "chart-items"), index)
        for index, key in enumerate(keys)
    }
    app_rows = [
        (key, rule_index, children, terminals_match)
        for key in keys
        for rule_index, children, terminals_match in applications[key]
    ]
    return _build_parse_forest(
        grammar,
        namespace,
        collapse_units,
        keys,
        root_key,
        references,
        app_rows,
        grammar_input,
    )


type _ChartKey = tuple[QualifiedName, int, int]
type _ChartApplication = tuple[int, tuple[_ChartKey, ...], bool]


def _deduce_chart(
    declaration: GrammarDeclaration,
    recognition_rules: tuple[tuple[int, QualifiedName, GrammarPattern], ...],
    tokens: tuple[str, ...],
    *,
    collapse_units: bool,
) -> tuple[list[_ChartKey], dict[_ChartKey, list[_ChartApplication]]]:
    """Deduce every chart item and its production applications."""
    applications: dict[
        tuple[QualifiedName, int, int],
        list[tuple[int, tuple[tuple[QualifiedName, int, int], ...], bool]],
    ] = {}
    size = len(tokens)
    keys = [
        (nonterminal, start, end)
        for width in range(size + 1)
        for nonterminal in declaration.nonterminals
        for start in range(size - width + 1)
        for end in (start + width,)
    ]
    for key in keys:
        left, start, end = key
        choices = applications.setdefault(key, [])
        for rule_index, candidate_left, source in recognition_rules:
            if candidate_left != left:
                continue
            for children, terminals_match in _candidate_matches(
                source,
                tokens,
                start,
                end,
                allow_empty_holes=not collapse_units,
            ):
                application = (rule_index, children, terminals_match)
                choices.append(application)
        if not choices:
            choices.append((-1, (), False))
    return keys, applications


def _build_parse_forest(  # noqa: PLR0915 -- one ordered graph construction
    grammar: LoweredGrammar,
    namespace: str,
    collapse_units: bool,
    keys: list[_ChartKey],
    root_key: _ChartKey,
    references: dict[_ChartKey, ItemRef],
    app_rows: list[tuple[_ChartKey, int, tuple[_ChartKey, ...], bool]],
    grammar_input: GrammarInput,
) -> ParseForest:
    """Construct the opcode program and bound recognition fold for a chart."""
    declaration = grammar.declaration
    names = {
        local: _name(namespace, local)
        for local in (
            "chart-items",
            "applications",
            "input-tokens",
            "realizations",
            "target-pieces",
            "provenance-values",
            "alternatives",
            "children",
            "production-application",
            "target-expansion",
            "realization-alternatives",
            "realization-expansion",
            "input-provenance",
            "realization-provenance",
            "local-factor",
            "weight",
            "kind",
            "text",
            "input-index",
            "rule-index",
            "nonterminal",
            "start",
            "end",
        )
    }
    _check_provenance_name_conflicts(declaration, set(names.values()), "parse-forest")
    opcodes: list[Opcode] = [
        DeclareNamespace(NamespaceDeclaration("chart", namespace)),
        *_provenance_namespace_opcodes(declaration, namespace),
    ]
    opcodes.extend(
        (
            DeclareTier(TierDeclaration(names["chart-items"], "Chart items")),
            DeclareTier(
                TierDeclaration(names["applications"], "Production applications")
            ),
            DeclareTier(TierDeclaration(names["input-tokens"], "Input tokens")),
            DeclareTier(TierDeclaration(names["realizations"], "Realizations")),
            DeclareTier(TierDeclaration(names["target-pieces"], "Target pieces")),
            DeclareTier(
                TierDeclaration(names["provenance-values"], "Provenance values")
            ),
            DeclareRelation(
                SimpleRelationDeclaration(
                    _name(namespace, "chart-membership"),
                    names["chart-items"],
                    _name(namespace, "chart-item-type"),
                )
            ),
            DeclareRelation(
                SimpleRelationDeclaration(
                    _name(namespace, "application-membership"),
                    names["applications"],
                    _name(namespace, "application-type"),
                )
            ),
            *(
                DeclareRelation(
                    SimpleRelationDeclaration(
                        _name(namespace, f"{local}-membership"),
                        names[local],
                        _name(namespace, f"{local}-type"),
                    )
                )
                for local in (
                    "input-tokens",
                    "realizations",
                    "target-pieces",
                    "provenance-values",
                )
            ),
        )
    )
    for relation, left, right, acyclic in (
        (
            "alternatives",
            _name(namespace, "chart-item-type"),
            _name(namespace, "application-type"),
            True,
        ),
        (
            "children",
            _name(namespace, "application-type"),
            _name(namespace, "chart-item-type"),
            True,
        ),
        (
            "realization-alternatives",
            _name(namespace, "input-tokens-type"),
            _name(namespace, "realizations-type"),
            True,
        ),
    ):
        opcodes.append(
            DeclareRelation(
                BipartiteRelationDeclaration(
                    names[relation], left, right, acyclic=acyclic
                )
            )
        )
    item_side = (RelationEndpointKind.ITEM,)
    for relation, sources, targets, distinct_targets in (
        (
            "production-application",
            (names["applications"],),
            (names["chart-items"],),
            False,
        ),
        (
            "target-expansion",
            (names["applications"],),
            (names["chart-items"], names["input-tokens"], names["target-pieces"]),
            False,
        ),
        (
            "realization-expansion",
            (names["realizations"],),
            (names["target-pieces"],),
            True,
        ),
        (
            "input-provenance",
            (names["input-tokens"],),
            (names["provenance-values"],),
            True,
        ),
        (
            "realization-provenance",
            (names["realizations"],),
            (names["provenance-values"],),
            True,
        ),
    ):
        opcodes.append(
            DeclareRelation(
                PolyadicRelationDeclaration(
                    names[relation],
                    RelationSideDeclaration(item_side, sources, 1, 1),
                    RelationSideDeclaration(item_side, targets, 0, None, True),
                    unique_sources=True,
                    distinct_targets=distinct_targets,
                    acyclic=True,
                )
            )
        )
    for local, value_type in (
        ("local-factor", XsdType.BOOLEAN),
        ("weight", XsdType.DECIMAL),
        ("kind", XsdType.STRING),
        ("text", XsdType.STRING),
        ("nonterminal", XsdType.STRING),
        ("input-index", XsdType.INTEGER),
        ("rule-index", XsdType.INTEGER),
        ("start", XsdType.INTEGER),
        ("end", XsdType.INTEGER),
    ):
        opcodes.append(
            DeclareAttribute(
                AttributeDeclaration(names[local], AttributeDomain.ITEM, value_type)
            )
        )
    opcodes.extend(
        DeclareAttribute(
            AttributeDeclaration(name, AttributeDomain.ITEM, XsdType.STRING)
        )
        for name in _provenance_names(declaration)
    )

    def add_item(
        reference: ItemRef,
        values: tuple[tuple[str, XsdType, str], ...],
    ) -> None:
        """Append one item and its scalar values to the chart program."""
        opcodes.append(AddItem(reference.tier, Item()))
        opcodes.extend(
            AttachValue(
                AttributeDomain.ITEM,
                reference,
                AttributeValue(names[local], value_type, lexical),
            )
            for local, value_type, lexical in values
        )

    piece_index = 0
    realization_index = 0
    provenance_index = 0

    def add_ordered_provenance(
        owner: ItemRef,
        relation: QualifiedName,
        values: tuple[str, ...],
        kind: str,
    ) -> None:
        """Retain one ordered plain-string provenance list in the graph."""
        nonlocal provenance_index
        references: list[ItemRef] = []
        for value in values:
            reference = ItemRef(names["provenance-values"], provenance_index)
            provenance_index += 1
            references.append(reference)
            add_item(
                reference,
                (
                    ("kind", XsdType.STRING, kind),
                    ("text", XsdType.STRING, value),
                ),
            )
        opcodes.append(
            Relate(PolyadicRelationInstance(relation, (owner,), tuple(references)))
        )

    input_references: list[ItemRef] = []
    for input_index, token in enumerate(grammar_input.tokens):
        token_reference = ItemRef(names["input-tokens"], input_index)
        input_references.append(token_reference)
        add_item(
            token_reference,
            (
                ("local-factor", XsdType.BOOLEAN, "true"),
                ("weight", XsdType.DECIMAL, "0"),
                ("kind", XsdType.STRING, "input-token"),
                ("text", XsdType.STRING, token.symbol),
                ("input-index", XsdType.INTEGER, str(input_index)),
            ),
        )
        add_ordered_provenance(
            token_reference,
            names["input-provenance"],
            token.provenance,
            "input-provenance",
        )
        for realization in token.realization:
            realization_reference = ItemRef(names["realizations"], realization_index)
            realization_index += 1
            add_item(
                realization_reference,
                (
                    ("local-factor", XsdType.BOOLEAN, "true"),
                    (
                        "weight",
                        XsdType.DECIMAL,
                        "0" if realization.weight is None else str(realization.weight),
                    ),
                    ("kind", XsdType.STRING, "realization"),
                    ("input-index", XsdType.INTEGER, str(input_index)),
                ),
            )
            add_ordered_provenance(
                realization_reference,
                names["realization-provenance"],
                realization.provenance,
                "realization-provenance",
            )
            pieces: list[ItemRef] = []
            for text in realization.tokens:
                piece = ItemRef(names["target-pieces"], piece_index)
                piece_index += 1
                pieces.append(piece)
                add_item(
                    piece,
                    (
                        ("local-factor", XsdType.BOOLEAN, "true"),
                        ("weight", XsdType.DECIMAL, "0"),
                        ("kind", XsdType.STRING, "realization-piece"),
                        ("text", XsdType.STRING, text),
                        ("input-index", XsdType.INTEGER, str(input_index)),
                    ),
                )
            opcodes.append(
                Relate(
                    RelationInstance(
                        names["realization-alternatives"],
                        token_reference,
                        realization_reference,
                    )
                )
            )
            opcodes.append(
                Relate(
                    PolyadicRelationInstance(
                        names["realization-expansion"],
                        (realization_reference,),
                        tuple(pieces),
                    )
                )
            )
    for key in keys:
        reference = references[key]
        values = (
            ("local-factor", XsdType.BOOLEAN, "true"),
            ("weight", XsdType.DECIMAL, "0"),
            ("kind", XsdType.STRING, "chart-item"),
            ("nonterminal", XsdType.STRING, str(key[0])),
            ("start", XsdType.INTEGER, str(key[1])),
            ("end", XsdType.INTEGER, str(key[2])),
        )
        add_item(reference, values)
    for app_index, (parent, rule_index, children, terminals_match) in enumerate(
        app_rows
    ):
        app = ItemRef(names["applications"], app_index)
        add_item(
            app,
            (
                (
                    "local-factor",
                    XsdType.BOOLEAN,
                    "true" if terminals_match else "false",
                ),
                (
                    "weight",
                    XsdType.DECIMAL,
                    "0"
                    if rule_index < 0
                    else declaration.rules[rule_index].effective_weight.lexical,
                ),
                ("kind", XsdType.STRING, "production-application"),
                ("start", XsdType.INTEGER, str(rule_index)),
                ("rule-index", XsdType.INTEGER, str(rule_index)),
            ),
        )
        if rule_index >= 0:
            opcodes.extend(
                AttachValue(AttributeDomain.ITEM, app, value)
                for value in declaration.rules[rule_index].provenance
            )
        child_refs = tuple(references[child] for child in children)
        opcodes.append(
            Relate(RelationInstance(names["alternatives"], references[parent], app))
        )
        opcodes.extend(
            Relate(RelationInstance(names["children"], app, child))
            for child in child_refs
        )
        opcodes.append(
            Relate(
                PolyadicRelationInstance(
                    names["production-application"], (app,), child_refs
                )
            )
        )
        target_references: list[ItemRef] = []
        if rule_index >= 0 and not collapse_units:
            rule = declaration.rules[rule_index]
            source_holes = [
                element for element in rule.source if isinstance(element, GrammarHole)
            ]
            source_variables = [element.variable.lexical for element in source_holes]
            target_variables = [
                element.variable.lexical
                for element in rule.target
                if isinstance(element, GrammarHole)
            ]
            if (
                len(set(source_variables)) == len(source_variables)
                and len(set(target_variables)) == len(target_variables)
                and not rule.awaited_variables
            ):
                bindings = {
                    hole.variable.lexical: child
                    for hole, child in zip(source_holes, child_refs, strict=True)
                }
                cursor = parent[1]
                terminal_positions: dict[str, list[int]] = {}
                child_keys = iter(children)
                for element in rule.source:
                    if isinstance(element, GrammarTerminal):
                        terminal_positions.setdefault(element.text.lexical, []).append(
                            cursor
                        )
                        cursor += 1
                    else:
                        cursor = next(child_keys)[2]
                for element in rule.target:
                    if isinstance(element, GrammarHole):
                        target_references.append(bindings[element.variable.lexical])
                        continue
                    positions = terminal_positions.get(element.text.lexical, [])
                    if len(positions) == 1:
                        target_references.append(input_references[positions[0]])
                        continue
                    piece = ItemRef(names["target-pieces"], piece_index)
                    piece_index += 1
                    target_references.append(piece)
                    add_item(
                        piece,
                        (
                            ("local-factor", XsdType.BOOLEAN, "true"),
                            ("weight", XsdType.DECIMAL, "0"),
                            ("kind", XsdType.STRING, "literal-piece"),
                            ("text", XsdType.STRING, element.text.lexical),
                        ),
                    )
        opcodes.append(
            Relate(
                PolyadicRelationInstance(
                    names["target-expansion"], (app,), tuple(target_references)
                )
            )
        )
    program = Program(tuple(opcodes))
    graph = program.unroll().graph
    root = references[root_key]
    fold = FoldDeclaration(
        "grammar-recognition",
        graph,
        AttributeValuation(
            "local factor",
            names["local-factor"],
            (names["chart-items"], names["applications"]),
        ),
        BOOLEAN,
        lambda value, _label: cast(bool, value),
        (
            FoldTransition(names["alternatives"], ChildCombination.OR),
            FoldTransition(names["children"], ChildCombination.AND),
        ),
        roots=(root,),
    )
    return ParseForest(
        graph, program, root, fold, declaration, collapse_units, grammar_input
    )


def _forest_names(forest: ParseForest | TargetLattice) -> dict[str, QualifiedName]:
    return {
        local: _name(forest.root.tier.namespace, local)
        for local in (
            "chart-items",
            "applications",
            "input-tokens",
            "realizations",
            "target-pieces",
            "provenance-values",
            "alternatives",
            "children",
            "production-application",
            "target-expansion",
            "realization-alternatives",
            "realization-expansion",
            "input-provenance",
            "realization-provenance",
            "local-factor",
            "weight",
            "kind",
            "text",
            "nonterminal",
            "start",
            "end",
            "input-index",
            "rule-index",
        )
    }


def _chart_path_index(value: str, index: int, path: CanonicalPath) -> int:
    if value == "0" or (
        value.isascii() and value.isdecimal() and not value.startswith("0")
    ):
        return int(value)
    code = (
        PathRefusalCode.NONCANONICAL_SEGMENT
        if value.isdecimal() and value != ""
        else PathRefusalCode.INVALID_SEGMENT
    )
    raise PathRefusal(
        code,
        PathOffender(text=str(path), path=path, segment_index=index, segment=value),
    )


def _item_attribute(graph: Graph, reference: ItemRef, local: str) -> AttributeValue:
    item = next(
        tier.items[reference.index]
        for tier in graph.tiers
        if tier.declaration.name == reference.tier
    )
    return _scalar_attribute(
        next(value for value in item.attributes if value.name.local_name == local)
    )


def _item_named_attribute(
    graph: Graph, reference: ItemRef, name: QualifiedName
) -> AttributeValue:
    """Read one scalar item attribute by its complete qualified name."""
    item = next(
        tier.items[reference.index]
        for tier in graph.tiers
        if tier.declaration.name == reference.tier
    )
    return _scalar_attribute(
        next(value for value in item.attributes if value.name == name)
    )


def _item_label(reference: ItemRef) -> str:
    """Return the structural label used by fold witnesses."""
    return f"{reference.tier.namespace}:{reference.tier.local_name}:{reference.index}"


def _count_fold(forest: ParseForest) -> FoldDeclaration[int]:
    names = _forest_names(forest)
    return FoldDeclaration(
        "grammar-derivation-count",
        forest.graph,
        AttributeValuation(
            "terminal match",
            names["local-factor"],
            (names["chart-items"], names["applications"]),
        ),
        COUNTING,
        lambda value, _label: 1 if cast(bool, value) else 0,
        (
            FoldTransition(names["alternatives"], ChildCombination.OR),
            FoldTransition(names["children"], ChildCombination.AND),
        ),
        roots=(forest.root,),
    )


def _best_fold(forest: ParseForest, output_cap: int) -> FoldDeclaration[PathValue]:
    names = _forest_names(forest)
    tiers = (names["chart-items"], names["applications"])
    valid_labels = {
        f"{reference.tier.namespace}:{reference.tier.local_name}:{reference.index}"
        for reference in forest.graph.canonical_items()
        if reference.tier in tiers
        and _item_attribute(forest.graph, reference, "local-factor").lexical == "true"
    }

    def lift(value: object, label: str) -> PathValue:
        """Lift a valid local weight and annihilate a terminal mismatch."""
        if label not in valid_labels:
            return PATH.zero
        return (cast(Decimal, value), ((label,),))

    return FoldDeclaration(
        "grammar-best-derivation",
        forest.graph,
        AttributeValuation(
            "rule weight",
            names["weight"],
            tiers,
        ),
        PATH,
        lift,
        (
            FoldTransition(names["alternatives"], ChildCombination.OR),
            FoldTransition(names["children"], ChildCombination.AND),
        ),
        roots=(forest.root,),
        output_cap=output_cap,
        ranked_output=True,
    )


def _generation_fold(
    forest: ParseForest, output_cap: int, root: ItemRef | None = None
) -> FoldDeclaration[PathValue]:
    """Build an experimental bounded fold over retained target expansions."""
    names = _forest_names(forest)
    tiers = (
        names["chart-items"],
        names["applications"],
        names["input-tokens"],
        names["realizations"],
        names["target-pieces"],
    )
    valid_labels = {
        f"{reference.tier.namespace}:{reference.tier.local_name}:{reference.index}"
        for reference in forest.graph.canonical_items()
        if reference.tier in tiers
        and _item_attribute(forest.graph, reference, "local-factor").lexical == "true"
    }

    def lift(value: object, label: str) -> PathValue:
        """Lift target weights while annihilating invalid chart applications."""
        if label not in valid_labels:
            return PATH.zero
        return (cast(Decimal, value), ((label,),))

    return FoldDeclaration(
        "experimental-grammar-target-best",
        forest.graph,
        AttributeValuation("target weight", names["weight"], tiers),
        PATH,
        lift,
        (
            FoldTransition(names["alternatives"], ChildCombination.OR),
            FoldTransition(names["target-expansion"], ChildCombination.AND),
            FoldTransition(names["realization-alternatives"], ChildCombination.OR),
            FoldTransition(names["realization-expansion"], ChildCombination.AND),
        ),
        roots=(forest.root if root is None else root,),
        output_cap=output_cap,
        ranked_output=True,
    )


def _source_coverage(
    grammar_input: GrammarInput, start: int, end: int
) -> tuple[SourceSpan, ...]:
    """Return the raw coverage set for one half-open chart token span."""
    selected = grammar_input.tokens[start:end]
    spans = tuple(token.span for token in selected if token.span is not None)
    if len(spans) != len(selected):
        return (SourceSpan(None, start, end),)
    if not spans:
        if grammar_input.tokens and all(
            token.span is not None for token in grammar_input.tokens
        ):
            neighbor = (
                grammar_input.tokens[start].span
                if start < len(grammar_input.tokens)
                else grammar_input.tokens[-1].span
            )
            assert neighbor is not None
            anchor = (
                neighbor.origin if start < len(grammar_input.tokens) else neighbor.end
            )
            return (SourceSpan(neighbor.partition, anchor, anchor),)
        return (SourceSpan(None, start, end),)
    partitions = {span.partition for span in spans}
    if len(partitions) == 1:
        return (
            SourceSpan(
                spans[0].partition,
                min(span.origin for span in spans),
                max(span.end for span in spans),
            ),
        )
    return spans


def _generated_derivation(  # noqa: PLR0915 -- one structural witness scan
    forest: TargetLattice,
    value: PathValue,
    witness: tuple[str, ...],
) -> GeneratedDerivation:
    """Materialize target pieces and applications from one structural witness."""
    grammar_input = forest.input
    names = _forest_names(forest)
    references_by_label = {
        _item_label(reference): reference
        for reference in forest.graph.canonical_items()
    }
    parent_by_application = {
        cast(ItemRef, relation.right): cast(ItemRef, relation.left)
        for relation in forest.graph.relations
        if relation.declaration == names["alternatives"]
    }
    applications: list[RuleApplication] = []
    applications_by_label: dict[str, RuleApplication] = {}
    coverage_by_label: dict[str, tuple[SourceSpan, ...]] = {}
    for label in witness:
        reference = references_by_label[label]
        if reference.tier != names["applications"]:
            continue
        rule_index = int(_item_attribute(forest.graph, reference, "rule-index").lexical)
        parent = parent_by_application[reference]
        declared = forest.declaration.rules[rule_index].provenance
        start = int(_item_attribute(forest.graph, parent, "start").lexical)
        end = int(_item_attribute(forest.graph, parent, "end").lexical)
        coverage = _source_coverage(grammar_input, start, end)
        application = RuleApplication(
            rule_index,
            tuple(
                _item_named_attribute(forest.graph, reference, item.name)
                for item in declared
            ),
            coverage[0] if len(coverage) == 1 else SourceSpan(None, start, end),
            label,
        )
        applications.append(application)
        applications_by_label[label] = application
        coverage_by_label[label] = coverage

    expansion_sources: dict[ItemRef, tuple[ItemRef, ...]] = {}
    realization_by_piece: dict[ItemRef, ItemRef] = {}
    provenance_by_owner: dict[tuple[QualifiedName, ItemRef], tuple[str, ...]] = {}
    for relation in forest.graph.polyadic_relations:
        source = cast(ItemRef, relation.sources[0])
        targets = tuple(cast(ItemRef, target) for target in relation.targets)
        if relation.declaration == names["target-expansion"]:
            expansion_sources[source] = targets
        elif relation.declaration == names["realization-expansion"]:
            realization_by_piece.update((piece, source) for piece in targets)
        elif relation.declaration in (
            names["input-provenance"],
            names["realization-provenance"],
        ):
            provenance_by_owner[(relation.declaration, source)] = tuple(
                _item_attribute(forest.graph, target, "text").lexical
                for target in targets
            )
    input_by_realization = {
        cast(ItemRef, relation.right): cast(ItemRef, relation.left)
        for relation in forest.graph.relations
        if relation.declaration == names["realization-alternatives"]
    }
    introducing_candidates: dict[ItemRef, set[str]] = {}
    for application_reference, targets in expansion_sources.items():
        label = _item_label(application_reference)
        for target in targets:
            introducing_candidates.setdefault(target, set()).add(label)
    witness_positions = {label: index for index, label in enumerate(witness)}
    pieces: list[TargetPiece] = []
    for position, label in enumerate(witness):
        reference = references_by_label[label]
        if reference.tier != names["target-pieces"]:
            continue
        realization = realization_by_piece.get(reference)
        input_provenance: tuple[str, ...] = ()
        source_reference: ItemRef | None = None
        spans: tuple[SourceSpan, ...] = ()
        owner = reference
        if realization is not None:
            token = input_by_realization[realization]
            owner = token
            input_provenance = provenance_by_owner.get(
                (names["input-provenance"], token), ()
            ) + provenance_by_owner.get(
                (names["realization-provenance"], realization), ()
            )
            input_index = int(
                _item_attribute(forest.graph, token, "input-index").lexical
            )
            input_token = grammar_input.tokens[input_index]
            source_reference = input_token.source
            spans = () if input_token.span is None else (input_token.span,)
        candidates = introducing_candidates.get(owner, set())
        introducing_label = max(
            (
                candidate
                for candidate in candidates
                if candidate in applications_by_label
                and witness_positions[candidate] < position
            ),
            key=witness_positions.__getitem__,
        )
        pieces.append(
            TargetPiece(
                _item_attribute(forest.graph, reference, "text").lexical,
                label,
                applications_by_label[introducing_label],
                input_provenance,
                source_reference,
                coverage_by_label[introducing_label] if realization is None else spans,
            )
        )
    return GeneratedDerivation(
        str(value[0]),
        tuple(pieces),
        tuple(applications),
        witness,
    )


def _validate_generation(declaration: GrammarDeclaration) -> None:
    """Refuse grammar shapes whose experimental target meaning is ambiguous."""
    for rule_index, rule in enumerate(declaration.rules):
        for role, pattern in (("source", rule.source), ("target", rule.target)):
            variables = [
                element.variable.lexical
                for element in pattern
                if isinstance(element, GrammarHole)
            ]
            repeated = next(
                (variable for variable in variables if variables.count(variable) > 1),
                None,
            )
            if repeated is not None:
                raise ValueError(
                    f"experimental generation refuses repeated {role} hole "
                    f"variable {repeated!r} in rule {rule_index}"
                )
        if rule.awaited_variables:
            raise ValueError(
                "experimental generation does not materialize awaited variables "
                f"in rule {rule_index}"
            )
        source_terminals = [
            element.text.lexical
            for element in rule.source
            if isinstance(element, GrammarTerminal)
        ]
        for element in rule.target:
            if (
                isinstance(element, GrammarTerminal)
                and source_terminals.count(element.text.lexical) > 1
            ):
                raise ValueError(
                    f"experimental generation rule {rule_index} target terminal "
                    f"{element.text.lexical!r} could echo more than one source occurrence"
                )


def target_lattice(
    forest: ParseForest,
    input: GrammarInput | None = None,
    *,
    root: _ChartKey | None = None,
) -> TargetLattice:
    """Return an experimental keep-all target view over one retained forest."""
    if forest.collapsed:
        raise ValueError(
            "target_lattice requires a parse forest built with collapse_units=False"
        )
    _validate_generation(forest.declaration)
    if forest.input is not None and input is not None and input != forest.input:
        raise ValueError("target_lattice input disagrees with the parse forest input")
    grammar_input = forest.input if input is None else input
    if grammar_input is None:
        raise ValueError("target_lattice requires the parse forest's grammar input")
    root_reference = forest.root if root is None else _resolve_chart_root(forest, root)
    fold = _generation_fold(forest, 1, root_reference)
    return TargetLattice(
        forest.graph,
        root_reference,
        fold,
        grammar_input,
        forest.declaration,
        _has_reachable_cycle(fold),
    )


def _has_reachable_cycle(fold: FoldDeclaration[PathValue]) -> bool:
    """Report whether a fold root reaches one of its already-computed cyclic SCCs."""
    dependency_graph = fold._dependency_graph()
    cyclic_items = {
        item for component in dependency_graph.cyclic_components for item in component
    }
    pending = list(dependency_graph.roots)
    reached: set[ItemRef] = set()
    while pending:
        item = pending.pop()
        if item in reached:
            continue
        if item in cyclic_items:
            return True
        reached.add(item)
        pending.extend(dependency_graph.adjacency[item])
    return False


def _resolve_chart_root(forest: ParseForest, root: _ChartKey) -> ItemRef:
    """Resolve one nonterminal and token span to its retained chart item."""
    chart_key_size = 3
    if (
        type(root) is not tuple
        or len(root) != chart_key_size
        or not isinstance(root[0], QualifiedName)
        or type(root[1]) is not int
        or type(root[2]) is not int
        or root[1] < 0
        or root[2] < root[1]
    ):
        raise ValueError(
            "target_lattice root must be a (QualifiedName, start, end) chart key"
        )
    names = _forest_names(forest)
    found = next(
        (
            reference
            for reference in forest.graph.canonical_items()
            if reference.tier == names["chart-items"]
            and _item_attribute(forest.graph, reference, "nonterminal").lexical
            == str(root[0])
            and _item_attribute(forest.graph, reference, "start").lexical
            == str(root[1])
            and _item_attribute(forest.graph, reference, "end").lexical == str(root[2])
        ),
        None,
    )
    if found is None:
        raise ValueError(f"target_lattice root {root!r} is not a chart item")
    return found


def generate(
    grammar: LoweredGrammar | ParseForest,
    input_tokens: Sequence[str] | GrammarInput | None = None,
    *,
    count: int = 1,
) -> GenerationResult:
    """Return up to ``count`` experimental target materializations."""
    forest = _forest(grammar, input_tokens, "generate")
    return target_lattice(forest).best(count)


def _forest(
    grammar: LoweredGrammar | ParseForest,
    input_tokens: Sequence[str] | GrammarInput | None,
    operation: str,
) -> ParseForest:
    if isinstance(grammar, ParseForest):
        if input_tokens is not None:
            raise ValueError("a prebuilt parse forest does not accept input tokens")
        if grammar.collapsed:
            raise ValueError(
                f"{operation} requires a parse forest built with collapse_units=False"
            )
        return grammar
    if input_tokens is None:
        raise ValueError("a lowered grammar requires input tokens")
    return recognize(grammar, input_tokens, collapse_units=False)


def count(
    grammar: LoweredGrammar | ParseForest,
    input_tokens: Sequence[str] | None = None,
) -> int:
    """Return the derivation count from a new or previously built forest."""
    forest = _forest(grammar, input_tokens, "count")
    return _count_fold(forest).run().value


def _best_derivations(
    forest: ParseForest, output_cap: int
) -> tuple[BestDerivation, ...]:
    if output_cap < 1:
        raise ValueError(f"best derivation count {output_cap!r} must be positive")
    result = _best_fold(forest, output_cap).run()
    ranked = cast(
        tuple[tuple[PathValue, tuple[str, ...]], ...], result.ranked_witnesses
    )
    return tuple(BestDerivation(str(value[0]), witness) for value, witness in ranked)


def best(
    grammar: LoweredGrammar | ParseForest,
    input_tokens: Sequence[str] | GrammarInput | None = None,
    count: int = 1,
) -> tuple[BestDerivation, ...]:
    """Return folded derivations by exact cost, choosing canonical paths on ties."""
    forest = _forest(grammar, input_tokens, "best")
    return _best_derivations(forest, count)


__all__ = [
    "CHART_NAMESPACE",
    "COMPLETE_BOUNDARY",
    "GRAMMAR_NAMESPACE",
    "BestDerivation",
    "GeneratedDerivation",
    "GenerationResult",
    "GrammarChartProfile",
    "GrammarDeclaration",
    "GrammarHole",
    "GrammarInput",
    "GrammarInputToken",
    "GrammarRule",
    "GrammarTerminal",
    "LoweredGrammar",
    "ParseForest",
    "Realization",
    "TargetLattice",
    "TargetPiece",
    "best",
    "count",
    "generate",
    "grammar_loads",
    "lower_grammar",
    "recognize",
    "target_lattice",
]
