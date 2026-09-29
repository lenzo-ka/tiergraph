"""Frozen value predicates, binding, evaluation, and strict JSON data."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, NoReturn, Protocol, cast

from tiergraph.core import (
    Attribute,
    AttributeDeclaration,
    AttributeValue,
    BoundaryRef,
    Graph,
    ItemRef,
    JsonType,
    JsonValue,
    NamespaceDeclaration,
    QualifiedName,
    Refusal,
    RefusalStage,
    XsdType,
    _canonical_lexical,
)
from tiergraph.machine import _decode_qname
from tiergraph.schema import _refuse_field_set
from tiergraph.wire import _object, _parsed_json, _string

if TYPE_CHECKING:
    from tiergraph.selection import Node, NodeSet

_BARE_FORBIDDEN = frozenset("!\"#%&'()*,/:<=>?@[\\]^{|}~∅")
_MISSING = object()
_MAX_REGEX_BYTES = 65_536
_MAX_REGEX_NESTING = 256
_MAX_REGEX_POSITIONS = 100_000
_MAX_REPEAT_COUNT = 10_000
_PAIR_SIZE = 2
_WHITE_SPACE_SINGLETONS = frozenset(
    {0x0020, 0x0085, 0x00A0, 0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000}
)
_WHITE_SPACE_RANGES = ((0x0009, 0x000D), (0x2000, 0x200A))


def _is_bare(text: str) -> bool:
    return (
        bool(text)
        and not text.startswith(".")
        and all(
            not character.isspace() and character not in _BARE_FORBIDDEN
            for character in text
        )
    )


@dataclass(frozen=True, slots=True)
class Bare:
    """Carry an unquoted token until its cell supplies a type at bind."""

    text: str

    def __post_init__(self) -> None:
        if not _is_bare(self.text):
            raise ValueError(f"Bare text {self.text!r} is not a bare token")


@dataclass(frozen=True, slots=True)
class Double:
    """Carry one xsd:double by canonical lexical identity."""

    lexical: str

    def __post_init__(self) -> None:
        try:
            lexical = _canonical_lexical(XsdType.DOUBLE, self.lexical)
        except ValueError as error:
            raise ValueError(
                f"Double lexical {self.lexical!r} is not xsd:double"
            ) from error
        object.__setattr__(self, "lexical", lexical)


type Literal = str | int | Decimal | bool | None | Double | Bare


@dataclass(frozen=True, slots=True)
class Cell:
    """Name one declared attribute and an optional JSON pointer beneath it."""

    attribute: QualifiedName
    pointer: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(key, str) for key in self.pointer):
            raise TypeError("Cell pointer keys must be strings")


@dataclass(frozen=True, slots=True)
class Current:
    """Name the current JSON element and an optional pointer beneath it."""

    pointer: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(key, str) for key in self.pointer):
            raise TypeError("Current pointer keys must be strings")


type Operand = Cell | Current


class Order(StrEnum):
    """Name an exact ordered comparison."""

    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="


class Quantifier(StrEnum):
    """Quantify a predicate over the elements of a JSON array."""

    ANY = "any"
    ALL = "all"
    NONE = "none"


def _literal_key(value: Literal) -> tuple[type[object], object]:
    if not _is_literal(value):
        raise TypeError(
            "predicate literals are str, int, Decimal, bool, None, Double, or Bare"
        )
    return type(value), value


def _is_literal(value: object) -> bool:
    return value is None or type(value) in (str, int, Decimal, bool, Double, Bare)


@dataclass(frozen=True, slots=True)
class Has:
    """Test whether an operand is present, carrying its missing-cell spelling."""

    operand: Operand
    alias: str

    def __post_init__(self) -> None:
        if not _is_bare(self.alias) or self.alias == "∅":
            raise ValueError(
                f"Has alias {self.alias!r} is not a declarable missing-cell alias"
            )


@dataclass(frozen=True, slots=True, eq=False)
class Equals:
    """Test exact typed membership in a nonempty literal tuple."""

    operand: Operand
    values: tuple[Literal, ...]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("Equals needs at least one value")
        seen: set[tuple[type[object], object]] = set()
        for value in self.values:
            key = _literal_key(value)
            if key in seen:
                raise ValueError(f"Equals lists {_literal_text(value)!r} twice")
            seen.add(key)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Equals)
            and self.operand == other.operand
            and tuple(_literal_key(value) for value in self.values)
            == tuple(_literal_key(value) for value in other.values)
        )

    def __hash__(self) -> int:
        return hash(
            (Equals, self.operand, tuple(_literal_key(value) for value in self.values))
        )


@dataclass(frozen=True, slots=True, eq=False)
class Compare:
    """Apply one exact numeric ordering relation."""

    operand: Operand
    order: Order
    value: Literal

    def __post_init__(self) -> None:
        _literal_key(self.value)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Compare)
            and self.operand == other.operand
            and self.order is other.order
            and _literal_key(self.value) == _literal_key(other.value)
        )

    def __hash__(self) -> int:
        return hash((Compare, self.operand, self.order, _literal_key(self.value)))


@dataclass(frozen=True, slots=True)
class Matches:
    """Fullmatch a string with the facility's regular-language subset."""

    operand: Operand
    regex: str

    def __post_init__(self) -> None:
        _compile_regex(self.regex)


@dataclass(frozen=True, slots=True)
class Elements:
    """Quantify a predicate over one JSON array operand."""

    operand: Operand
    quantifier: Quantifier
    body: Predicate


@dataclass(frozen=True, slots=True)
class And:
    """Intersect zero or at least two predicate decisions."""

    args: tuple[Predicate, ...]

    def __post_init__(self) -> None:
        if len(self.args) == 1:
            raise ValueError(
                "And needs zero or at least two arguments; use the argument itself"
            )
        if any(isinstance(argument, And) for argument in self.args):
            raise ValueError(
                "And may not directly contain an And; flatten its arguments into this one"
            )


@dataclass(frozen=True, slots=True)
class Or:
    """Union zero or at least two predicate decisions."""

    args: tuple[Predicate, ...]

    def __post_init__(self) -> None:
        if len(self.args) == 1:
            raise ValueError(
                "Or needs zero or at least two arguments; use the argument itself"
            )
        if any(isinstance(argument, Or) for argument in self.args):
            raise ValueError(
                "Or may not directly contain an Or; flatten its arguments into this one"
            )


@dataclass(frozen=True, slots=True)
class Not:
    """Complement one completed two-valued predicate decision."""

    arg: Predicate


type Predicate = Has | Equals | Compare | Matches | Elements | And | Or | Not


@dataclass(frozen=True, slots=True)
class PredicateSyntax:
    """Declare names, a default prefix, and missing-cell text spellings."""

    namespaces: tuple[NamespaceDeclaration, ...]
    default_prefix: str | None = None
    missing_aliases: tuple[str, ...] = ("none",)
    vocabularies: tuple[tuple[QualifiedName, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        prefixes = [binding.prefix for binding in self.namespaces]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("PredicateSyntax namespace prefixes must be unique")
        if self.default_prefix is not None and self.default_prefix not in prefixes:
            raise ValueError(
                f"default prefix {self.default_prefix!r} is not declared in this syntax"
            )
        seen: set[str] = set()
        for alias in self.missing_aliases:
            if not _is_bare(alias) or alias == "∅":
                raise ValueError(
                    f"missing alias {alias!r} is not a declarable missing-cell alias"
                )
            if alias in seen:
                raise ValueError(f"missing alias {alias!r} is declared twice")
            seen.add(alias)

    @classmethod
    def for_graph(
        cls,
        graph: Graph,
        *,
        default_prefix: str | None = None,
        missing_aliases: tuple[str, ...] = ("none",),
        vocabularies: tuple[tuple[QualifiedName, tuple[str, ...]], ...] = (),
    ) -> PredicateSyntax:
        """Build syntax from a graph's namespace declarations."""
        return cls(graph.namespaces, default_prefix, missing_aliases, vocabularies)


@dataclass(frozen=True, slots=True)
class _EmptyRegex:
    pass


@dataclass(frozen=True, slots=True)
class _AtomRegex:
    matcher: _Matcher


@dataclass(frozen=True, slots=True)
class _SequenceRegex:
    parts: tuple[_Regex, ...]


@dataclass(frozen=True, slots=True)
class _AlternateRegex:
    parts: tuple[_Regex, ...]


@dataclass(frozen=True, slots=True)
class _RepeatRegex:
    body: _Regex
    minimum: int
    maximum: int | None


type _Regex = _EmptyRegex | _AtomRegex | _SequenceRegex | _AlternateRegex | _RepeatRegex


@dataclass(frozen=True, slots=True)
class _Matcher:
    kind: str
    value: object = None
    negated: bool = False

    def accepts(self, character: str) -> bool:
        """Return whether this matcher accepts one character."""
        answer: bool
        if self.kind == "literal":
            answer = character == self.value
        elif self.kind == "dot":
            answer = character != "\n"
        elif self.kind == "digit":
            answer = unicodedata.category(character) == "Nd"
        elif self.kind == "word":
            category = unicodedata.category(character)
            answer = (
                category[0] in {"L", "M"}
                or category in {"Nd", "Nl", "Pc"}
                or character in {"\u200c", "\u200d"}
            )
        elif self.kind == "space":
            codepoint = ord(character)
            answer = codepoint in _WHITE_SPACE_SINGLETONS or any(
                low <= codepoint <= high for low, high in _WHITE_SPACE_RANGES
            )
        else:
            members = cast(tuple[_Matcher | tuple[str, str], ...], self.value)
            answer = any(
                member.accepts(character)
                if isinstance(member, _Matcher)
                else member[0] <= character <= member[1]
                for member in members
            )
        return not answer if self.negated else answer


class _RegexParser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.index = 0

    def parse(self) -> _Regex:
        """Parse and validate the complete regular expression."""
        if len(self.source.encode("utf-8")) > _MAX_REGEX_BYTES:
            self.refuse(0, "regex source exceeds 65536 bytes")
        # A group frame holds its completed alternatives and current sequence.
        # Keeping those frames here rather than on Python's call stack makes the
        # declared 256-level grammar bound independent of the interpreter's
        # recursion limit.
        frames: list[tuple[int, list[_Regex], list[_Regex]]] = [(-1, [], [])]
        while self.index < len(self.source):
            character = self.source[self.index]
            if character == "|":
                frames[-1][1].append(self._sequence(frames[-1][2]))
                frames[-1][2].clear()
                self.index += 1
                continue
            if character == ")":
                if len(frames) == 1:
                    self.refuse(self.index, "unexpected ')' in regex")
                frames[-1][1].append(self._sequence(frames[-1][2]))
                start, alternatives, _ = frames.pop()
                del start
                self.index += 1
                body = self._alternatives(alternatives)
                frames[-1][2].append(self._repeat_after(body))
                continue
            if character == "(":
                start = self.index
                self.index += 1
                if len(frames) > _MAX_REGEX_NESTING:
                    self.refuse(start, "regex nests deeper than 256")
                self._group_prefix(start)
                frames.append((start, [], []))
                continue
            frames[-1][2].append(self._repeat_after(self._atom()))
        if len(frames) != 1:
            self.refuse(frames[-1][0], "'(' is never closed")
        frames[0][1].append(self._sequence(frames[0][2]))
        result = self._alternatives(frames[0][1])
        positions = _regex_positions(result)
        if positions > _MAX_REGEX_POSITIONS:
            self.refuse(
                0,
                f"regex unrolls to {positions} positions; limit {_MAX_REGEX_POSITIONS}",
            )
        return result

    @staticmethod
    def _sequence(parts: list[_Regex]) -> _Regex:
        """Make one sequence node from a frame's current parts."""
        if not parts:
            return _EmptyRegex()
        if len(parts) == 1:
            return parts[0]
        return _SequenceRegex(tuple(parts))

    @staticmethod
    def _alternatives(parts: list[_Regex]) -> _Regex:
        """Make one alternation node from a frame's completed branches."""
        if len(parts) == 1:
            return parts[0]
        return _AlternateRegex(tuple(parts))

    def _group_prefix(self, start: int) -> None:
        """Validate and consume a group extension after its opening parenthesis."""
        if not self.take("?"):
            return
        if self.take(":"):
            return
        if self.source.startswith(("=", "!", "<=", "<!"), self.index):
            token = (
                "(?"
                + self.source[
                    self.index : self.index
                    + (2 if self.source[self.index] == "<" else 1)
                ]
            )
            self.refuse(start, f"{token!r} is lookaround, which the subset refuses")
        if self.source.startswith("P<", self.index):
            self.refuse(start, "'(?P<' is a named group; write (?:…)")
        if self.source.startswith("<", self.index):
            self.refuse(start, "'(?<' is a named group; write (?:…)")
        flag = self.source[self.index : self.index + 1]
        if flag:
            self.refuse(
                self.index,
                f"flag {flag!r} is not defined; no regex flag is defined yet, "
                "so write (?:…) for a plain group",
            )
        self.refuse(start, "regex group extension is incomplete")

    def _atom(self) -> _Regex:
        """Parse one non-group regex atom."""
        start = self.index
        character = self.source[self.index]
        if character in "^$":
            self.refuse(
                start,
                f"{character!r} is an anchor; '~' already matches the whole string",
            )
        if character == ".":
            self.index += 1
            return _AtomRegex(_Matcher("dot"))
        if character == "[":
            return _AtomRegex(self.character_class())
        if character == "\\":
            return _AtomRegex(self.escape())
        if character in "*+?{":
            self.refuse(start, f"{character!r} has nothing before it to repeat")
        self.index += 1
        return _AtomRegex(_Matcher("literal", character))

    def _repeat_after(self, result: _Regex) -> _Regex:
        """Consume at most one quantifier following an already parsed atom."""
        if self.index >= len(self.source):
            return result
        start = self.index
        character = self.source[self.index]
        minimum: int
        maximum: int | None
        token: str
        if character == "*":
            self.index += 1
            minimum, maximum, token = 0, None, "*"
        elif character == "+":
            self.index += 1
            minimum, maximum, token = 1, None, "+"
        elif character == "?":
            self.index += 1
            minimum, maximum, token = 0, 1, "?"
        elif character == "{":
            minimum, maximum, token = self.counter()
        else:
            return result
        if self.index < len(self.source) and self.source[self.index] in "?+":
            suffix = self.source[self.index]
            self.refuse(
                start,
                f"{token + suffix!r} is a lazy quantifier; every match is decided, "
                "so lazy and possessive quantifiers are refused",
            )
        return _RepeatRegex(result, minimum, maximum)

    def counter(self) -> tuple[int, int | None, str]:
        """Parse a braced repetition count."""
        start = self.index
        self.index += 1
        close = self.source.find("}", self.index)
        if close < 0:
            self.refuse(start, "'{' is never closed")
        content = self.source[self.index : close]
        self.index = close + 1
        compact = "".join(character for character in content if not character.isspace())
        pieces = compact.split(",")
        maximum: int | None
        if len(pieces) == 1 and pieces[0].isdigit():
            minimum = maximum = int(pieces[0])
        elif (
            len(pieces) == _PAIR_SIZE
            and (not pieces[0] or pieces[0].isdigit())
            and (not pieces[1] or pieces[1].isdigit())
            and (pieces[0] or pieces[1])
        ):
            minimum = int(pieces[0]) if pieces[0] else 0
            maximum = int(pieces[1]) if pieces[1] else None
        else:
            self.refuse(
                start, f"{self.source[start : close + 1]!r} is not a counted quantifier"
            )
        largest = max(minimum, maximum if maximum is not None else minimum)
        if largest > _MAX_REPEAT_COUNT:
            self.refuse(start, f"repeat count {largest} exceeds limit 10000")
        if maximum is not None and minimum > maximum:
            token = self.source[start : close + 1]
            self.refuse(
                start,
                f"{token!r} has minimum {minimum} greater than maximum {maximum}",
            )
        return minimum, maximum, self.source[start : close + 1]

    def escape(self, *, in_class: bool = False) -> _Matcher:
        """Parse one supported escape sequence."""
        start = self.index
        self.index += 1
        if self.index >= len(self.source):
            self.refuse(start, "'\\' is not an escape in the subset")
        character = self.source[self.index]
        self.index += 1
        if character.isdigit() and not in_class:
            token = "\\" + character
            self.refuse(
                start,
                f"'{token}' is a backreference, which a regular language cannot express",
            )
        if character in "bBAZzG" and not in_class:
            token = "\\" + character
            self.refuse(
                start,
                f"'{token}' is an anchor; '~' already matches the whole string",
            )
        if character == "p":
            self.refuse(
                start,
                "'\\p' property classes are not in the subset; write a class such as \\w or [a-z]",
            )
        if character in "dDsSwW":
            kind = {"d": "digit", "s": "space", "w": "word"}[character.lower()]
            return _Matcher(kind, negated=character.isupper())
        if character in "tnr":
            return _Matcher("literal", {"t": "\t", "n": "\n", "r": "\r"}[character])
        if character == "x" and self.take("{"):
            close = self.source.find("}", self.index)
            hexadecimal = self.source[self.index : close] if close >= 0 else ""
            if (
                close < 0
                or not hexadecimal
                or any(c not in "0123456789abcdefABCDEF" for c in hexadecimal)
            ):
                self.refuse(start, "'\\x' needs a braced hexadecimal code point")
            try:
                value = chr(int(hexadecimal, 16))
            except ValueError as error:
                self.refuse(start, "'\\x' names no Unicode code point", error)
            self.index = close + 1
            return _Matcher("literal", value)
        escaped = ".*+?()[]{}|\\^$-/"
        if character in escaped:
            return _Matcher("literal", character)
        token = "\\" + character
        self.refuse(start, f"'{token}' is not an escape in the subset")

    def character_class(self) -> _Matcher:
        """Parse one bracketed character class."""
        start = self.index
        self.index += 1
        negated = self.take("^")
        members: list[_Matcher | tuple[str, str]] = []
        first = True
        while self.index < len(self.source) and (
            first or self.source[self.index] != "]"
        ):
            left = (
                self.escape(in_class=True)
                if self.source[self.index] == "\\"
                else self.literal_class_character()
            )
            first = False
            if (
                self.index < len(self.source) - 1
                and self.source[self.index] == "-"
                and self.source[self.index + 1] != "]"
                and left.kind == "literal"
            ):
                self.index += 1
                right_start = self.index
                right = (
                    self.escape(in_class=True)
                    if self.source[self.index] == "\\"
                    else self.literal_class_character()
                )
                if right.kind != "literal":
                    self.refuse(
                        self.index, "a character-class range needs literal endpoints"
                    )
                low, high = cast(str, left.value), cast(str, right.value)
                if low > high:
                    self.refuse(
                        right_start,
                        f"character-class range {low}-{high} is reversed",
                    )
                members.append((low, high))
            else:
                members.append(left)
        if not self.take("]"):
            self.refuse(start, "'[' is never closed")
        return _Matcher("class", tuple(members), negated)

    def literal_class_character(self) -> _Matcher:
        """Consume one literal character inside a class."""
        character = self.source[self.index]
        self.index += 1
        return _Matcher("literal", character)

    def take(self, token: str) -> bool:
        """Consume a token when it occurs at the current offset."""
        if self.source.startswith(token, self.index):
            self.index += len(token)
            return True
        return False

    @staticmethod
    def refuse(offset: int, message: str, cause: Exception | None = None) -> NoReturn:
        """Raise a source-located regex construction refusal."""
        error = ValueError(f"regex at offset {offset}: {message}")
        if cause is None:
            raise error
        raise error from cause


def _regex_positions(regex: _Regex) -> int:
    if isinstance(regex, _EmptyRegex):
        return 0
    if isinstance(regex, _AtomRegex):
        return 1
    if isinstance(regex, _SequenceRegex | _AlternateRegex):
        return sum(_regex_positions(part) for part in regex.parts)
    factor = regex.maximum if regex.maximum is not None else regex.minimum + 1
    return _regex_positions(regex.body) * factor


@dataclass(slots=True)
class _Nfa:
    epsilon: list[set[int]]
    transitions: list[list[tuple[_Matcher, int]]]
    start: int
    accept: int

    @classmethod
    def build(cls, regex: _Regex) -> _Nfa:
        """Build an NFA for a parsed regular expression."""
        result = cls([], [], 0, 0)
        result.start, result.accept = result.fragment(regex)
        return result

    def state(self) -> int:
        """Allocate and return one NFA state."""
        result = len(self.epsilon)
        self.epsilon.append(set())
        self.transitions.append([])
        return result

    def fragment(self, regex: _Regex) -> tuple[int, int]:
        """Build an NFA fragment and return its endpoints."""
        if isinstance(regex, _EmptyRegex):
            start, end = self.state(), self.state()
            self.epsilon[start].add(end)
            return start, end
        if isinstance(regex, _AtomRegex):
            start, end = self.state(), self.state()
            self.transitions[start].append((regex.matcher, end))
            return start, end
        if isinstance(regex, _SequenceRegex):
            start, end = self.fragment(regex.parts[0])
            for part in regex.parts[1:]:
                next_start, next_end = self.fragment(part)
                self.epsilon[end].add(next_start)
                end = next_end
            return start, end
        if isinstance(regex, _AlternateRegex):
            start, end = self.state(), self.state()
            for part in regex.parts:
                branch_start, branch_end = self.fragment(part)
                self.epsilon[start].add(branch_start)
                self.epsilon[branch_end].add(end)
            return start, end
        return self.repeat_fragment(regex)

    def repeat_fragment(self, regex: _RepeatRegex) -> tuple[int, int]:
        """Build a bounded or unbounded repetition fragment."""
        start = self.state()
        cursor = start
        for _ in range(regex.minimum):
            body_start, body_end = self.fragment(regex.body)
            self.epsilon[cursor].add(body_start)
            cursor = body_end
        end = self.state()
        if regex.maximum is None:
            body_start, body_end = self.fragment(regex.body)
            self.epsilon[cursor].update((body_start, end))
            self.epsilon[body_end].update((body_start, end))
        else:
            for _ in range(regex.maximum - regex.minimum):
                body_start, body_end = self.fragment(regex.body)
                self.epsilon[cursor].update((body_start, end))
                cursor = body_end
            self.epsilon[cursor].add(end)
        return start, end

    def closure(self, states: set[int]) -> set[int]:
        """Compute the epsilon closure of a state set."""
        result = set(states)
        pending = list(states)
        while pending:
            state = pending.pop()
            for target in self.epsilon[state]:
                if target not in result:
                    result.add(target)
                    pending.append(target)
        return result

    def fullmatch(self, text: str) -> bool:
        """Return whether the NFA accepts the entire text."""
        states = self.closure({self.start})
        for character in text:
            states = self.closure(
                {
                    target
                    for state in states
                    for matcher, target in self.transitions[state]
                    if matcher.accepts(character)
                }
            )
        return self.accept in states


@lru_cache(maxsize=256)
def _compile_regex(source: str) -> _Nfa:
    return _Nfa.build(_RegexParser(source).parse())


def _literal_text(value: Literal) -> str:
    if isinstance(value, Bare):
        return value.text
    if isinstance(value, Double):
        return value.lexical
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)


def _json_kind(value: object) -> str:
    if value is None:
        return "null"
    return {
        bool: "boolean",
        int: "integer",
        float: "double",
        str: "string",
        list: "array",
        dict: "object",
    }[type(value)]


def _pointer(value: object, pointer: tuple[str, ...]) -> object:
    current = value
    for key in pointer:
        if type(current) is dict:
            mapping = cast(dict[str, object], current)
            if key not in mapping:
                return _MISSING
            current = mapping[key]
        elif (
            type(current) is list
            and key.isascii()
            and key.isdigit()
            and (key == "0" or not key.startswith("0"))
        ):
            values = cast(list[object], current)
            index = int(key)
            if index >= len(values):
                return _MISSING
            current = values[index]
        else:
            return _MISSING
    return current


def _find_declaration(graph: Graph, name: QualifiedName) -> AttributeDeclaration | None:
    return next(
        (
            candidate
            for candidate in graph.attribute_declarations
            if candidate.name == name
        ),
        None,
    )


def _display_name(graph: Graph, name: QualifiedName) -> str:
    prefix = next(
        (
            binding.prefix
            for binding in graph.namespaces
            if binding.namespace == name.namespace
        ),
        None,
    )
    return f"{prefix}:{name.local_name}" if prefix is not None else str(name)


def _operand_text(operand: Operand) -> str:
    root = operand.attribute.local_name if isinstance(operand, Cell) else "."
    return "/".join((root, *operand.pointer))


class _NodeLike(Protocol):
    @property
    def kind(self) -> object:
        """Return the node-kind discriminator."""
        ...

    @property
    def reference(self) -> object:
        """Return the node's graph reference."""
        ...


@dataclass(frozen=True, slots=True)
class _ItemNode:
    kind: str
    reference: ItemRef


def _kind(node: _NodeLike) -> str:
    kind = node.kind
    return cast(str, getattr(kind, "value", kind))


def _carrier_attributes(graph: Graph, node: _NodeLike) -> tuple[Attribute, ...]:
    reference = node.reference
    kind = _kind(node)
    if kind == "document":
        return graph.attributes
    if kind == "tier" and isinstance(reference, QualifiedName):
        return next(
            tier.attributes
            for tier in graph.tiers
            if tier.declaration.name == reference
        )
    if kind == "item" and isinstance(reference, ItemRef):
        tier = next(
            tier for tier in graph.tiers if tier.declaration.name == reference.tier
        )
        return tier.items[reference.index].attributes
    if kind == "boundary" and isinstance(reference, BoundaryRef):
        resolved = graph.resolve_boundary(reference)
        return next(
            (
                boundary.attributes
                for boundary in graph.boundary_values
                if graph.resolve_boundary(boundary.reference) == resolved
            ),
            (),
        )
    if kind == "relation_declaration" and isinstance(reference, QualifiedName):
        return next(
            declaration.attributes
            for declaration in graph.relation_declarations
            if declaration.name == reference
        )
    if kind == "relation_instance" and isinstance(reference, int):
        return graph.relations[reference].attributes
    if kind == "polyadic_relation_instance" and isinstance(reference, int):
        return graph.polyadic_relations[reference].attributes
    return ()


def _all_item_nodes(graph: Graph) -> Iterator[_ItemNode]:
    for reference in graph.canonical_items():
        yield _ItemNode("item", reference)


@dataclass(frozen=True, slots=True)
class _Context:
    node: _NodeLike
    current: object = _MISSING


@dataclass(frozen=True, slots=True)
class _Read:
    present: bool
    value: object = _MISSING
    value_type: XsdType | JsonType | None = None


@dataclass(frozen=True, slots=True)
class CompiledPredicate:
    """Hold a validated graph-free predicate ready to bind."""

    predicate: Predicate

    def bind(self, graph: Graph) -> BoundPredicate:
        """Validate names and type-impossible operations against one graph."""
        _Binder(graph).check(self.predicate, current_allowed=False)
        return BoundPredicate(self.predicate, graph)


@dataclass(frozen=True, slots=True)
class BoundPredicate:
    """Evaluate one predicate against nodes of its bound graph."""

    predicate: Predicate
    graph: Graph

    def holds(self, node: Node) -> bool:
        """Decide one graph node after evaluating every atom it can reach."""
        return self._decision(self.predicate, _Context(node))

    def select(self, candidates: NodeSet) -> NodeSet:
        """Return candidates that hold, retaining the candidate set's domain."""
        if candidates.graph is not self.graph:
            raise Refusal(
                RefusalStage.SEMANTICS,
                "predicate selection requires candidates from the bound graph",
            )
        selected = tuple(node for node in candidates.nodes if self.holds(node))
        return candidates.__class__(self.graph, selected)

    def _decision(self, predicate: Predicate, context: _Context) -> bool:
        cache: dict[Predicate, bool] = {}
        for atom in _atoms(predicate):
            cache[atom] = self._atom(atom, context)
        return _combine(predicate, cache)

    def _atom(self, atom: Predicate, context: _Context) -> bool:
        if isinstance(atom, Has):
            return self._read(atom.operand, context).present
        if isinstance(atom, Equals):
            reading = self._read(atom.operand, context)
            return reading.present and any(
                _equals(reading, value) for value in atom.values
            )
        if isinstance(atom, Compare):
            reading = self._read(atom.operand, context)
            if not reading.present:
                return False
            left = self._ordered_value(reading, atom, context.node)
            right = _ordered_literal(atom.value)
            return {
                Order.LT: left < right,
                Order.LE: left <= right,
                Order.GT: left > right,
                Order.GE: left >= right,
            }[atom.order]
        if isinstance(atom, Matches):
            reading = self._read(atom.operand, context)
            return (
                reading.present
                and type(reading.value) is str
                and _compile_regex(atom.regex).fullmatch(reading.value)
            )
        assert isinstance(atom, Elements)
        reading = self._read(atom.operand, context)
        if not reading.present:
            return atom.quantifier is not Quantifier.ANY
        if type(reading.value) is not list:
            kind = _json_kind(reading.value)
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"{_operand_text(atom.operand)}: item {self._node_label(context.node)!r} "
                f"stores a JSON {kind}; any, all and none need a JSON array",
            )
        decisions = tuple(
            self._decision(atom.body, _Context(context.node, element))
            for element in cast(list[object], reading.value)
        )
        if atom.quantifier is Quantifier.ANY:
            return any(decisions)
        if atom.quantifier is Quantifier.NONE:
            return not any(decisions)
        return not any(not decision for decision in decisions)

    def _read(self, operand: Operand, context: _Context) -> _Read:
        if isinstance(operand, Current):
            value = _pointer(context.current, operand.pointer)
            return _Read(value is not _MISSING, value, JsonType.JSON)
        value = next(
            (
                attribute
                for attribute in _carrier_attributes(self.graph, context.node)
                if attribute.name == operand.attribute
            ),
            None,
        )
        if value is None:
            return _Read(False)
        if isinstance(value, AttributeValue):
            return _Read(True, value.lexical, value.value_type)
        pointed = _pointer(value.to_value(), operand.pointer)
        return _Read(pointed is not _MISSING, pointed, JsonType.JSON)

    def _ordered_value(self, reading: _Read, atom: Compare, node: _NodeLike) -> Decimal:
        if reading.value_type in {XsdType.INTEGER, XsdType.DECIMAL}:
            return Decimal(cast(str, reading.value))
        if reading.value_type is JsonType.JSON and type(reading.value) is int:
            return Decimal(reading.value)
        kind = _json_kind(reading.value)
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"{_operand_text(atom.operand)}{atom.order.value}{_literal_text(atom.value)}: "
            f"item {self._node_label(node)!r} stores a JSON {kind} at this cell; "
            "ordered comparison needs a JSON integer",
        )

    def _node_label(self, node: _NodeLike) -> str:
        if _kind(node) == "item" and isinstance(node.reference, ItemRef):
            tier = next(
                tier
                for tier in self.graph.tiers
                if tier.declaration.name == node.reference.tier
            )
            return tier.items[node.reference.index].durable_id or str(
                node.reference.index
            )
        return str(node.reference)


class _Binder:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    def check(self, predicate: Predicate, *, current_allowed: bool) -> None:
        """Validate one predicate recursively against the bound graph."""
        if isinstance(predicate, And | Or):
            for argument in predicate.args:
                self.check(argument, current_allowed=current_allowed)
            return
        if isinstance(predicate, Not):
            self.check(predicate.arg, current_allowed=current_allowed)
            return
        if isinstance(predicate, Elements):
            self.operand(predicate.operand, current_allowed=current_allowed)
            declaration = self.declaration(predicate.operand)
            if declaration is not None and declaration.value_type is not JsonType.JSON:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"{_operand_text(predicate.operand)} is {declaration.value_type.value}; "
                    "any, all and none need a JSON array",
                )
            self.check(predicate.body, current_allowed=True)
            self.alias_collisions(predicate.body, predicate.operand)
            return
        operand = predicate.operand
        self.operand(operand, current_allowed=current_allowed)
        declaration = self.declaration(operand)
        if isinstance(predicate, Has):
            self.alias_collision(predicate)
            return
        if declaration is None:
            if isinstance(predicate, Compare):
                self.ordered_literal(predicate)
            return
        if isinstance(predicate, Equals):
            for value in predicate.values:
                self.equality_literal(predicate.operand, declaration, value)
        elif isinstance(predicate, Compare):
            self.ordered(predicate, declaration)
        elif (
            isinstance(predicate, Matches)
            and declaration.value_type is not XsdType.STRING
            and declaration.value_type is not JsonType.JSON
        ):
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"{_operand_text(predicate.operand)}~{json.dumps(predicate.regex)}: '~' needs "
                f"xsd:string or a JSON string, and {_display_name(self.graph, declaration.name)} "
                f"is xsd:{declaration.value_type.value}",
            )

    def operand(self, operand: Operand, *, current_allowed: bool) -> None:
        """Validate one operand in its current evaluation context."""
        if isinstance(operand, Current):
            if not current_allowed:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    "'.' names the current element or token; an item test names a cell, "
                    "as in class=vowel",
                )
            return
        declaration = self.declaration(operand)
        if declaration is None:
            raise Refusal(
                RefusalStage.REFERENCE,
                f"predicate names undeclared attribute {_display_name(self.graph, operand.attribute)}",
            )
        if operand.pointer and declaration.value_type is not JsonType.JSON:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"{_operand_text(operand)}: {_display_name(self.graph, operand.attribute)} "
                f"is xsd:{declaration.value_type.value}, and only a JSON cell has keys",
            )

    def declaration(self, operand: Operand) -> AttributeDeclaration | None:
        """Return the graph declaration named by an operand, if any."""
        if isinstance(operand, Current):
            return None
        return _find_declaration(self.graph, operand.attribute)

    def equality_literal(
        self,
        operand: Operand,
        declaration: AttributeDeclaration,
        value: Literal,
    ) -> None:
        """Validate one equality literal against an attribute declaration."""
        value_type = declaration.value_type
        if value_type is JsonType.JSON:
            return
        admitted = {
            XsdType.STRING: type(value) in (str, Bare),
            XsdType.INTEGER: type(value) in (int, Decimal, Bare),
            XsdType.DECIMAL: type(value) in (int, Decimal, Bare),
            XsdType.BOOLEAN: type(value) in (bool, Bare),
            XsdType.DOUBLE: type(value) in (Double, Bare),
        }[value_type]
        if not admitted:
            description = (
                f"{json.dumps(value)} is a quoted string"
                if type(value) is str
                else f"{_literal_text(value)} is {type(value).__name__}"
            )
            hint = (
                f"; write {_operand_text(operand)}={value}"
                if type(value) is str
                and value_type in {XsdType.INTEGER, XsdType.DECIMAL}
                else ""
            )
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"{_operand_text(operand)}={json.dumps(value) if type(value) is str else _literal_text(value)}: "
                f"{_display_name(self.graph, declaration.name)} is xsd:{value_type.value} "
                f"and {description}, so the test can never hold{hint}",
            )
        if isinstance(value, Bare):
            try:
                _canonical_lexical(value_type, value.text)
            except ValueError as error:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"{_operand_text(operand)}={value.text}: {value.text!r} is not an "
                    f"xsd:{value_type.value} value",
                ) from error

    def ordered(self, predicate: Compare, declaration: AttributeDeclaration) -> None:
        """Validate an ordered comparison against its declaration."""
        if declaration.value_type not in {
            XsdType.INTEGER,
            XsdType.DECIMAL,
            JsonType.JSON,
        }:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"{_operand_text(predicate.operand)}{predicate.order.value}{_literal_text(predicate.value)}: "
                "ordered comparison needs xsd:integer or xsd:decimal, and "
                f"{_display_name(self.graph, declaration.name)} is xsd:{declaration.value_type.value}",
            )
        self.ordered_literal(predicate)

    @staticmethod
    def ordered_literal(predicate: Compare) -> None:
        """Validate a literal independently of an operand declaration."""
        try:
            _ordered_literal(predicate.value)
        except (InvalidOperation, ValueError) as error:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"{_operand_text(predicate.operand)}{predicate.order.value}"
                f"{_literal_text(predicate.value)}: "
                f"{_literal_text(predicate.value)!r} is not an exact numeric "
                "comparison value",
            ) from error

    def alias_collision(self, predicate: Has) -> None:
        """Refuse a missing alias that collides with a stored cell value."""
        if isinstance(predicate.operand, Current):
            return
        bound = BoundPredicate(predicate, self.graph)
        for node in _all_item_nodes(self.graph):
            reading = bound._read(predicate.operand, _Context(node))
            if reading.present and reading.value == predicate.alias:
                self.raise_alias_collision(predicate.operand, predicate.alias)

    def alias_collisions(self, body: Predicate, array_operand: Operand) -> None:
        """Refuse missing aliases that collide inside stored arrays."""
        uses = tuple(
            atom
            for atom in _atoms(body)
            if isinstance(atom, Has) and isinstance(atom.operand, Current)
        )
        if not uses:
            return
        bound = BoundPredicate(body, self.graph)
        for node in _all_item_nodes(self.graph):
            array = bound._read(array_operand, _Context(node))
            if not array.present or type(array.value) is not list:
                continue
            for element in cast(list[object], array.value):
                for use in uses:
                    reading = bound._read(use.operand, _Context(node, element))
                    if reading.present and reading.value == use.alias:
                        self.raise_alias_collision(use.operand, use.alias)

    def raise_alias_collision(self, operand: Operand, alias: str) -> None:
        """Raise the shared missing-alias collision refusal."""
        text = _operand_text(operand)
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"{text}={alias}: {alias!r} is both the missing-cell alias and a stored "
            f"value of {text}; write {text}={json.dumps(alias)} for the value, or "
            "declare a different missing alias",
        )


def _ordered_literal(value: Literal) -> Decimal:
    if type(value) is int:
        return Decimal(value)
    if type(value) is Decimal:
        return value
    if isinstance(value, Bare):
        return Decimal(value.text)
    raise ValueError("ordered comparison needs an int, Decimal, or Bare decimal")


def _bare_json(value: Bare) -> Literal:
    try:
        parsed = json.loads(
            value.text,
            parse_float=Double,
            parse_constant=str,
        )
    except (json.JSONDecodeError, ValueError):
        return value.text
    if type(parsed) is int:
        return parsed
    if type(parsed) is bool:
        return parsed
    if parsed is None:
        return None
    if isinstance(parsed, Double):
        return parsed
    return value.text


def _equals(reading: _Read, literal: Literal) -> bool:
    if reading.value_type is JsonType.JSON:
        value: Literal = _bare_json(literal) if isinstance(literal, Bare) else literal
        if isinstance(value, Double):
            return type(reading.value) is float and Double(repr(reading.value)) == value
        if isinstance(value, Decimal):
            return False
        return type(reading.value) is type(value) and reading.value == value
    value_type = cast(XsdType, reading.value_type)
    lexical = cast(str, reading.value)
    if isinstance(literal, Bare):
        text = literal.text
        if value_type is XsdType.STRING:
            literal = text
        elif value_type in {XsdType.INTEGER, XsdType.DECIMAL}:
            literal = Decimal(_canonical_lexical(value_type, text))
        elif value_type is XsdType.BOOLEAN:
            literal = _canonical_lexical(value_type, text) == "true"
        else:
            literal = Double(text)
    if value_type is XsdType.STRING:
        return type(literal) is str and lexical == literal
    if value_type in {XsdType.INTEGER, XsdType.DECIMAL}:
        return type(literal) in (int, Decimal) and Decimal(lexical) == Decimal(
            cast(int | Decimal, literal)
        )
    if value_type is XsdType.BOOLEAN:
        return type(literal) is bool and (lexical == "true") is literal
    return isinstance(literal, Double) and lexical == literal.lexical


def _atoms(predicate: Predicate) -> Iterator[Predicate]:
    if isinstance(predicate, And | Or):
        for argument in predicate.args:
            yield from _atoms(argument)
    elif isinstance(predicate, Not):
        yield from _atoms(predicate.arg)
    else:
        yield predicate


def _combine(predicate: Predicate, cache: dict[Predicate, bool]) -> bool:
    if isinstance(predicate, And):
        return all(_combine(argument, cache) for argument in predicate.args)
    if isinstance(predicate, Or):
        return any(_combine(argument, cache) for argument in predicate.args)
    if isinstance(predicate, Not):
        return not _combine(predicate.arg, cache)
    return cache[predicate]


def compile_predicate(predicate: Predicate) -> CompiledPredicate:
    """Compile one frozen predicate without consulting a graph."""
    return CompiledPredicate(predicate)


def _operand_to_data(operand: Operand) -> dict[str, JsonValue]:
    if isinstance(operand, Cell):
        result: dict[str, JsonValue] = {"cell": operand.attribute.to_data()}
        if operand.pointer:
            result["pointer"] = list(operand.pointer)
        return result
    result = {"current": list(operand.pointer)}
    return result


def _literal_to_data(value: Literal) -> JsonValue:
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, Double):
        return {"double": value.lexical}
    if isinstance(value, Bare):
        return {"bare": value.text}
    return value


def predicate_to_data(predicate: Predicate) -> JsonValue:
    """Return strict JSON data for one predicate AST."""
    if isinstance(predicate, Has):
        return {
            "test": "has",
            "operand": _operand_to_data(predicate.operand),
            "alias": predicate.alias,
        }
    if isinstance(predicate, Equals):
        return {
            "test": "equals",
            "operand": _operand_to_data(predicate.operand),
            "values": [_literal_to_data(value) for value in predicate.values],
        }
    if isinstance(predicate, Compare):
        return {
            "test": "compare",
            "operand": _operand_to_data(predicate.operand),
            "order": predicate.order.value,
            "value": _literal_to_data(predicate.value),
        }
    if isinstance(predicate, Matches):
        return {
            "test": "matches",
            "operand": _operand_to_data(predicate.operand),
            "regex": predicate.regex,
        }
    if isinstance(predicate, Elements):
        return {
            "test": "elements",
            "operand": _operand_to_data(predicate.operand),
            "quantifier": predicate.quantifier.value,
            "body": predicate_to_data(predicate.body),
        }
    if isinstance(predicate, And | Or):
        return {
            "test": "and" if isinstance(predicate, And) else "or",
            "args": [predicate_to_data(argument) for argument in predicate.args],
        }
    return {"test": "not", "arg": predicate_to_data(predicate.arg)}


def predicate_loads(source: str | bytes) -> Predicate:
    """Decode one strict predicate JSON document."""
    return _decode_predicate(cast(JsonValue, _parsed_json(source)), "$")


def _decode_predicate(value: JsonValue, path: str) -> Predicate:
    node = cast(dict[str, JsonValue], _object(value, path))
    if "test" not in node:
        raise Refusal(RefusalStage.DISCRIMINATOR, f"{path} must contain 'test'")
    kind = node["test"]
    if not isinstance(kind, str):
        raise Refusal(RefusalStage.DISCRIMINATOR, f"{path}.test must be a string")
    declared: dict[str, tuple[set[str], set[str]]] = {
        "has": ({"test", "operand", "alias"}, {"test", "operand", "alias"}),
        "equals": ({"test", "operand", "values"}, {"test", "operand", "values"}),
        "compare": (
            {"test", "operand", "order", "value"},
            {"test", "operand", "order", "value"},
        ),
        "matches": ({"test", "operand", "regex"}, {"test", "operand", "regex"}),
        "elements": (
            {"test", "operand", "quantifier", "body"},
            {"test", "operand", "quantifier", "body"},
        ),
        "and": ({"test", "args"}, {"test", "args"}),
        "or": ({"test", "args"}, {"test", "args"}),
        "not": ({"test", "arg"}, {"test", "arg"}),
    }
    if kind not in declared:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"{path}.test has unknown test {kind!r}",
        )
    allowed, required = declared[kind]
    _refuse_field_set(node.keys(), allowed, required, path)
    if kind == "has":
        return Has(
            _decode_operand(node["operand"], f"{path}.operand"),
            _string(node["alias"], f"{path}.alias"),
        )
    if kind == "equals":
        raw_values = node["values"]
        if not isinstance(raw_values, list):
            raise Refusal(RefusalStage.SHAPE, f"{path}.values must be an array")
        return Equals(
            _decode_operand(node["operand"], f"{path}.operand"),
            tuple(
                _decode_literal(child, f"{path}.values[{index}]")
                for index, child in enumerate(raw_values)
            ),
        )
    if kind == "compare":
        order_text = _string(node["order"], f"{path}.order")
        try:
            order = Order(order_text)
        except ValueError as error:
            raise Refusal(
                RefusalStage.VALUE,
                f"{path}.order has invalid comparison order {order_text!r}",
            ) from error
        return Compare(
            _decode_operand(node["operand"], f"{path}.operand"),
            order,
            _decode_literal(node["value"], f"{path}.value"),
        )
    if kind == "matches":
        return Matches(
            _decode_operand(node["operand"], f"{path}.operand"),
            _string(node["regex"], f"{path}.regex"),
        )
    if kind == "elements":
        quantifier_text = _string(node["quantifier"], f"{path}.quantifier")
        try:
            quantifier = Quantifier(quantifier_text)
        except ValueError as error:
            raise Refusal(
                RefusalStage.VALUE,
                f"{path}.quantifier has invalid quantifier {quantifier_text!r}",
            ) from error
        return Elements(
            _decode_operand(node["operand"], f"{path}.operand"),
            quantifier,
            _decode_predicate(node["body"], f"{path}.body"),
        )
    if kind in {"and", "or"}:
        raw_args = node["args"]
        if not isinstance(raw_args, list):
            raise Refusal(RefusalStage.SHAPE, f"{path}.args must be an array")
        args = tuple(
            _decode_predicate(child, f"{path}.args[{index}]")
            for index, child in enumerate(raw_args)
        )
        return And(args) if kind == "and" else Or(args)
    return Not(_decode_predicate(node["arg"], f"{path}.arg"))


def _decode_operand(value: JsonValue, path: str) -> Operand:
    node = cast(dict[str, JsonValue], _object(value, path))
    if "cell" in node:
        _refuse_field_set(node.keys(), {"cell", "pointer"}, {"cell"}, path)
        pointer = _decode_pointer(node.get("pointer", []), f"{path}.pointer")
        return Cell(_decode_qname(node["cell"], f"{path}.cell"), pointer)
    if "current" in node:
        _refuse_field_set(node.keys(), {"current"}, {"current"}, path)
        return Current(_decode_pointer(node["current"], f"{path}.current"))
    raise Refusal(
        RefusalStage.DISCRIMINATOR,
        f"{path} must contain exactly one of 'cell' or 'current'",
    )


def _decode_pointer(value: JsonValue, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise Refusal(RefusalStage.SHAPE, f"{path} must be an array")
    return tuple(_string(key, f"{path}[{index}]") for index, key in enumerate(value))


def _decode_literal(value: JsonValue, path: str) -> Literal:
    if value is None or type(value) in (str, int, bool):
        return cast(str | int | bool | None, value)
    if type(value) is float:
        raise Refusal(
            RefusalStage.VALUE,
            f"{path} is a JSON double; write an object with a 'double' lexical form",
        )
    node = cast(dict[str, JsonValue], _object(value, path))
    kinds = {"decimal", "double", "bare"} & node.keys()
    if len(kinds) != 1:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"{path} must contain exactly one of 'decimal', 'double' or 'bare'",
        )
    kind = next(iter(kinds))
    _refuse_field_set(node.keys(), {kind}, {kind}, path)
    text = _string(node[kind], f"{path}.{kind}")
    if kind == "bare":
        return Bare(text)
    if kind == "double":
        return Double(text)
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise Refusal(
            RefusalStage.VALUE,
            f"{path}.decimal is not a decimal lexical form",
        ) from error


# The text layer imports the frozen AST above and is imported only after that AST
# and its helpers exist, keeping the graph-free grammar separate from evaluation.
from tiergraph.predicate_text import (  # noqa: E402, I001
    format_predicate,
    parse_predicate,
    parse_predicate_at,
)


__all__ = [
    "And",
    "Bare",
    "BoundPredicate",
    "Cell",
    "Compare",
    "CompiledPredicate",
    "Current",
    "Double",
    "Elements",
    "Equals",
    "Has",
    "Literal",
    "Matches",
    "Not",
    "Operand",
    "Or",
    "Order",
    "Predicate",
    "PredicateSyntax",
    "Quantifier",
    "compile_predicate",
    "format_predicate",
    "parse_predicate",
    "parse_predicate_at",
    "predicate_loads",
    "predicate_to_data",
]
