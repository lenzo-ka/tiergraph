"""Text parsing and formatting for regular sequence patterns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn

from tiergraph.core import Refusal, RefusalStage
from tiergraph.machine import MAX_REPEAT_COUNT
from tiergraph.match import (
    AltPattern,
    AtomPattern,
    EndPattern,
    FocusPattern,
    Pattern,
    RepeatPattern,
    SeqPattern,
    StartPattern,
)
from tiergraph.predicate import (
    And,
    PredicateSyntax,
    format_predicate,
    parse_predicate_at,
)

_PATTERN_NOTATION = frozenset("{(|/_. *+?^$")
_SEQUENCE_PRECEDENCE = 2


def _sequence(parts: Sequence[Pattern]) -> Pattern:
    flattened = tuple(
        child
        for part in parts
        for child in (part.parts if isinstance(part, SeqPattern) else (part,))
    )
    return flattened[0] if len(flattened) == 1 else SeqPattern(flattened)


def _alternation(parts: Sequence[Pattern]) -> Pattern:
    flattened = tuple(
        child
        for part in parts
        for child in (part.parts if isinstance(part, AltPattern) else (part,))
    )
    return flattened[0] if len(flattened) == 1 else AltPattern(flattened)


def _first_anchor(pattern: Pattern) -> str:
    if isinstance(pattern, StartPattern):
        return "^"
    if isinstance(pattern, EndPattern):
        return "$"
    if isinstance(pattern, (SeqPattern, AltPattern)):
        for part in pattern.parts:
            try:
                return _first_anchor(part)
            except LookupError:
                pass
    raise LookupError("pattern has no anchor")  # pragma: no cover - caller invariant


@dataclass(frozen=True, slots=True)
class _Primary:
    pattern: Pattern
    group_text: str | None = None
    single_group_element: bool = False


class _PatternParser:
    def __init__(
        self, text: str, start: int, end: int, syntax: PredicateSyntax
    ) -> None:
        self.text = text
        self.index = start
        self.end = end
        self.syntax = syntax

    def parse(self) -> Pattern:
        """Parse exactly the configured source interval."""
        return self.pattern_body()

    def _finish_repeat(
        self,
        primary: _Primary,
        quantifier_start: int,
        quantifier: tuple[int, int | None, str],
    ) -> Pattern:
        minimum, maximum, token = quantifier
        next_token = self._quantifier_token(self.index)
        if next_token:
            combined = token + next_token
            if next_token in {"?", "+"}:
                self.refuse(
                    quantifier_start,
                    f"{combined!r} is a lazy quantifier; patterns decide every span, so lazy and possessive quantifiers are refused",
                )
            self.refuse(
                self.index,
                f"{next_token!r} stacks a second quantifier on one operand; group first, as in ({{p}}{{2}})*",
            )
        if maximum == 0:
            self.refuse(
                quantifier_start,
                f"{token!r} matches only the empty sequence; remove it",
            )
        if maximum is not None and minimum > maximum:
            self.refuse(
                quantifier_start,
                f"{token!r} has minimum {minimum} greater than maximum {maximum}",
            )
        largest = minimum if maximum is None else max(minimum, maximum)
        if largest > MAX_REPEAT_COUNT:
            self.refuse(
                quantifier_start,
                f"repeat count {largest} exceeds limit {MAX_REPEAT_COUNT}",
            )
        try:
            return RepeatPattern(primary.pattern, minimum, maximum)
        except ValueError as error:
            anchor = _first_anchor(primary.pattern)
            self.refuse(
                quantifier_start,
                f"{anchor!r} is inside a repetition; an anchor matches a position, not an item, and cannot repeat",
                error,
            )

    def primary(self) -> _Primary:
        """Parse one unquantified pattern primary."""
        offset = self.index
        character = self.peek()
        if character == ".":
            self.index += 1
            return _Primary(AtomPattern(And(())))
        if character == "^":
            self.index += 1
            return _Primary(StartPattern())
        if character == "$":
            self.index += 1
            return _Primary(EndPattern())
        if character == "{":
            if self.text.startswith("{}", offset, self.end):
                self.refuse(
                    offset,
                    "'{}' is an empty item test; write '.' for one item of any kind",
                )
            predicate, end = parse_predicate_at(
                self.text, offset + 1, self.syntax, terminator="}"
            )
            self.index = end + 1
            return _Primary(AtomPattern(predicate))
        if character == "(":
            return self.group()
        self.unexpected()

    def pattern_body(self) -> Pattern:  # noqa: PLR0915 -- grammar scenario
        """Parse one complete pattern body, including focus notation."""
        self.space()
        if self.index == self.end:
            self.refuse(self.index, "empty pattern; write '.' for one item of any kind")
        target_start = self.index
        target = self.alternation({"/", "_"})
        self.space()
        if self.peek() == "_":
            self.refuse(
                self.index,
                "'_' marks the focus site and belongs after '/', as in {t} / {a} _",
            )
        if self.peek() != "/":
            if self.index != self.end:
                self.unexpected()
            return target
        slash = self.index
        if self._top_level_token(target_start, slash, "|"):
            self.refuse(
                slash,
                "alternatives before '/' must be grouped, as in ({a} | {b}) / _",
            )
        self.index += 1
        self.space()
        left_start = self.index
        if self.index == self.end:
            self.refuse(slash, "'/' needs exactly one '_' marking the focus site")
        if self.peek() == "/":
            self.refuse(self.index, "a pattern has at most one '/'")
        left = None if self.peek() == "_" else self.alternation({"_", "/"})
        if left is not None and self._top_level_token(left_start, self.index, "|"):
            self.refuse(
                self.index,
                "alternatives in a context must be grouped, as in ({a} | {b}) _",
            )
        self.space()
        if self.peek() == "/":
            self.refuse(self.index, "a pattern has at most one '/'")
        if self.peek() != "_":
            self.refuse(slash, "'/' needs exactly one '_' marking the focus site")
        self.index += 1
        self.space()
        if self.peek() == "/":
            self.refuse(self.index, "a pattern has at most one '/'")
        right_start = self.index
        right = None if self.index == self.end else self.alternation({"/", "_"})
        if right is not None and self._top_level_token(right_start, self.index, "|"):
            self.refuse(
                self.index,
                "alternatives in a context must be grouped, as in ({a} | {b}) _",
            )
        self.space()
        if self.peek() == "/":
            self.refuse(self.index, "a pattern has at most one '/'")
        if self.peek() == "_":
            self.refuse(slash, "'/' needs exactly one '_' marking the focus site")
        if self.index != self.end:
            self.unexpected()
        try:
            focus = FocusPattern(target)
        except ValueError as error:
            if "can match no item" in str(error):
                self.refuse(
                    slash,
                    "the focus can match no item; a focus must consume at least one item",
                    error,
                )
            anchor = _first_anchor(target)
            self.refuse(
                slash,
                f"{anchor!r} is inside the focus; write anchors in the context, as in {{t}} / {anchor} _",
                error,
            )
        parts = [part for part in (left, focus, right) if part is not None]
        return parts[0] if len(parts) == 1 else _sequence(parts)

    def group(self) -> _Primary:
        """Parse one parenthesized group."""
        opening = self.index
        self.index += 1
        if self.peek() == "?":
            self._flag_prefix(opening)
        self.space()
        if self.index == self.end:
            self.refuse(opening, "'(' is never closed")
        if self.peek() == ")":
            self.refuse(opening, "'()' is an empty group")
        if self.peek() == "_":
            self.refuse(
                self.index,
                "'_' cannot be grouped, repeated or alternated; it stands between the left and right context",
            )
        body = self.alternation({")", "_", "/"})
        self.space()
        if self.peek() == "_":
            self.refuse(
                self.index,
                "'_' cannot be grouped, repeated or alternated; it stands between the left and right context",
            )
        if self.peek() != ")":
            self.refuse(opening, "'(' is never closed")
        self.index += 1
        text = self.text[opening : self.index]
        return _Primary(body, text, not isinstance(body, (SeqPattern, AltPattern)))

    def alternation(self, stops: set[str]) -> Pattern:
        """Parse an alternation up to one of the requested stops."""
        parts = [self.sequence(stops | {"|"})]
        while True:
            self.space()
            if self.peek() != "|":
                break
            offset = self.index
            if self.text.startswith("||", offset, self.end):
                self.refuse(
                    offset,
                    "'||' is reserved for ordered choice; write '|' for alternation",
                )
            self.index += 1
            self.space()
            if self.index == self.end or self.peek() in stops | {"|", ")"}:
                self.refuse(
                    offset,
                    "'|' has an empty alternative; write {p}? for an optional part",
                )
            parts.append(self.sequence(stops | {"|"}))
        return _alternation(parts)

    def sequence(self, stops: set[str]) -> Pattern:
        """Parse a sequence up to one of the requested stops."""
        parts: list[Pattern] = []
        while True:
            self.space()
            character = self.peek()
            if not character or character in stops or character == ")":
                break
            token = self._quantifier_token(self.index)
            if token:
                if not parts:
                    self.refuse(
                        self.index,
                        f"{token!r} has nothing before it to repeat; write '.' for one item of any kind, or '.*' for any number of items",
                    )
                if token.startswith("{"):
                    self.refuse(
                        self.index,
                        f"{token!r} follows a space; a counted quantifier attaches directly, as in {{p}}{{2}}",
                    )
                self.refuse(
                    self.index,
                    f"{token!r} follows a space; a quantifier attaches directly to the item or group before it, as in {{p}}*; write '.' for one item of any kind",
                )
            parts.append(self.element())
        if not parts:
            self.unexpected()
        return _sequence(parts)

    def element(self) -> Pattern:
        """Parse one primary and its optional quantifier."""
        primary = self.primary()
        quantifier_start = self.index
        quantifier = self._take_quantifier()
        if primary.single_group_element and quantifier is None:
            group = primary.group_text
            assert group is not None
            self.refuse(
                quantifier_start - len(group),
                f"{group!r} groups one element and changes nothing; write {{seg=a}}? for an optional item or {{seg=a}} for a required one",
            )
        if quantifier is None:
            return primary.pattern
        return self._finish_repeat(primary, quantifier_start, quantifier)

    def _take_quantifier(self) -> tuple[int, int | None, str] | None:
        simple = {"?": (0, 1), "*": (0, None), "+": (1, None)}
        character = self.peek()
        if character in simple:
            self.index += 1
            minimum, maximum = simple[character]
            return minimum, maximum, character
        closing = self._counter_at(self.index)
        if closing is None:
            return None
        start = self.index
        self.index = closing + 1
        token = self.text[start : self.index]
        compact = "".join(c for c in token[1:-1] if not c.isspace())
        pieces = compact.split(",")
        valid = (
            len(pieces) in {1, 2}
            and any(pieces)
            and all(
                not piece or piece.isascii() and piece.isdigit() for piece in pieces
            )
        )
        if not valid:
            self.refuse(
                start,
                f"{token!r} is not a counted quantifier; write {{n}}, {{n,}}, {{,m}} or {{n,m}}",
            )
        if len(pieces) == 1:
            return int(pieces[0]), int(pieces[0]), token
        minimum = int(pieces[0]) if pieces[0] else 0
        maximum = int(pieces[1]) if pieces[1] else None
        return minimum, maximum, token

    def _counter_at(self, offset: int) -> int | None:
        if offset >= self.end or self.text[offset] != "{":
            return None
        closing = self.text.find("}", offset + 1, self.end)
        if closing < 0:
            return None
        interior = self.text[offset + 1 : closing]
        if not interior:
            return None
        if all(c.isdigit() or c == "," or c.isspace() for c in interior):
            return closing
        return None

    def _quantifier_token(self, offset: int) -> str:
        if offset < self.end and self.text[offset] in "*+?":
            return self.text[offset]
        closing = self._counter_at(offset)
        return "" if closing is None else self.text[offset : closing + 1]

    def _flag_prefix(self, opening: int) -> None:
        self.index += 1
        if self.peek() == ":":
            self.index += 1
            return
        for prefix in ("(?=", "(?<", "(?#", "(?1", "(?P<"):
            if self.text.startswith(prefix, opening, self.end):
                self.refuse(
                    opening,
                    f"{prefix!r} is not a flag group; '(?' opens (?flags:...) or (?flags)",
                )
        on: dict[str, int] = {}
        off: dict[str, int] = {}
        clearing = False
        hyphen = None
        while self.index < self.end and self.peek() not in {":", ")"}:
            character = self.peek()
            if character == "-" and not clearing:
                clearing, hyphen = True, self.index
                self.index += 1
                continue
            if not character.isascii() or not character.isalpha():
                token = self.text[opening : self.index + 1]
                self.refuse(
                    opening,
                    f"{token!r} is not a flag group; '(?' opens (?flags:...) or (?flags)",
                )
            target = off if clearing else on
            if character in target:
                fragment = self.text[opening : self.index + 2]
                self.refuse(
                    self.index, f"flag {character!r} is repeated in {fragment!r}"
                )
            other = on if clearing else off
            if character in other:
                fragment = self.text[opening : self.index + 2]
                self.refuse(
                    self.index,
                    f"flag {character!r} is both set and cleared in {fragment!r}",
                )
            target[character] = self.index
            self.index += 1
        if not on and not off:
            if hyphen is not None:
                self.refuse(
                    hyphen, "'-' clears no flag; write (?:...) for a plain group"
                )
            self.refuse(opening, "'(?)' sets no flag; remove it")
        letter, offset = next(iter(on.items())) if on else next(iter(off.items()))
        self.refuse(
            offset,
            f"flag {letter!r} is not defined; no pattern flag is defined yet, so write (?:...) or (...) for a plain group",
        )

    def space(self) -> None:
        """Advance over insignificant whitespace."""
        while self.index < self.end and self.text[self.index].isspace():
            self.index += 1

    def _top_level_token(self, start: int, end: int, token: str) -> bool:
        depth = braces = 0
        quoted = escaped = False
        for character in self.text[start:end]:
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
            elif braces:
                if character == '"':
                    quoted = True
                elif character == "}":
                    braces -= 1
            elif character == "{":
                braces += 1
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            elif not depth and character == token:
                return True
        return False

    def peek(self) -> str:
        """Return the current source character, or an empty sentinel."""
        return self.text[self.index] if self.index < self.end else ""

    def unexpected(self) -> NoReturn:
        """Refuse the current character with its literal diagnostic."""
        offset, character = self.index, self.peek()
        fixed = {
            ")": "')' closes no group",
            "]": "unexpected ']'",
            "#": "'#' is not pattern notation; write '^' for the start of the scope or '$' for its end",
            "[": "'[' is reserved; item tests go in braces, as in {class=vowel}",
            '"': 'a quoted string is an item value; item tests go in braces, as in {class="a"}',
            "&": "'&' conjoins item tests; write it inside one brace, as in {p & q}",
            "!": "'!' negates item tests; write it inside the brace, as in {!(p)}; a pattern has no complement",
            "\\": "'\\' escapes nothing at pattern level; literals go in braces, as in {seg=\"*\"}",
            "∅": "'∅' is reserved for the empty language, which a pattern cannot spell in this version",
            "%": "'%' is reserved",
            "=": "unexpected '='",
            "_": "'_' marks the focus site and belongs after '/', as in {t} / {a} _",
            "/": "'/' needs exactly one '_' marking the focus site",
        }
        if self.text.startswith("||", offset, self.end):
            self.refuse(
                offset,
                "'||' is reserved for ordered choice; write '|' for alternation",
            )
        if character == "|":
            self.refuse(
                offset,
                "'|' has an empty alternative; write {p}? for an optional part",
            )
        if self.text.startswith("&&", offset, self.end):
            self.refuse(offset, "'&&' is reserved for short-circuit conjunction")
        if character in fixed:
            self.refuse(offset, fixed[character])
        if character and character in "@~<>:":
            self.refuse(offset, f"{character!r} is reserved")
        if character and (character.isalnum() or character == "-"):
            end = offset + 1
            while end < self.end and (
                self.text[end].isalnum() or self.text[end] in "-:"
            ):
                end += 1
            word = self.text[offset:end]
            self.refuse(
                offset,
                f"{word!r} is a bare word; item tests go in braces, as in {{class={word}}}",
            )
        self.refuse(offset, f"unexpected {character!r}")

    @staticmethod
    def refuse(offset: int, message: str, cause: Exception | None = None) -> NoReturn:
        """Raise one source-located pattern-syntax refusal."""
        error = Refusal(RefusalStage.SYNTAX, f"pattern at offset {offset}: {message}")
        if cause is None:
            raise error
        raise error from cause


def _pattern_end(text: str, start: int, terminator: str | None) -> int:
    if start < 0 or start > len(text):
        raise ValueError(f"pattern start {start} is outside the text")
    if terminator is None:
        return len(text)
    if len(terminator) != 1 or terminator.isspace() or terminator in _PATTERN_NOTATION:
        raise ValueError(
            f"terminator {terminator!r} is pattern notation; choose a character the pattern grammar never uses outside braces"
        )
    depth = braces = 0
    quoted = escaped = False
    cursor = start
    while cursor < len(text):
        character = text[cursor]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif braces:
            if character == '"':
                quoted = True
            elif character == "}":
                braces -= 1
        elif character == "{":
            braces += 1
        elif character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        elif character == terminator and depth == 0:
            return cursor
        cursor += 1
    raise Refusal(
        RefusalStage.SYNTAX,
        f"pattern at offset {len(text)}: pattern text has no closing {terminator!r}",
    )


def parse_pattern(text: str, syntax: PredicateSyntax) -> Pattern:
    """Parse one complete regular sequence pattern."""
    return _PatternParser(text, 0, len(text), syntax).parse()


def parse_pattern_at(
    text: str,
    start: int,
    syntax: PredicateSyntax,
    terminator: str | None = None,
) -> tuple[Pattern, int]:
    """Parse until a requested terminator outside item tests and groups."""
    end = _pattern_end(text, start, terminator)
    return _PatternParser(text, start, end, syntax).parse(), end


def _plain(pattern: Pattern, syntax: PredicateSyntax, parent: int = 0) -> str:
    if isinstance(pattern, AtomPattern):
        if pattern.predicate == And(()):
            return "."
        try:
            return "{" + format_predicate(pattern.predicate, syntax) + "}"
        except ValueError as error:
            if "has no value-test text" not in str(error):
                raise
            name = type(pattern.predicate).__name__
            raise ValueError(f"pattern atom has no text form: {name}") from error
    if isinstance(pattern, StartPattern):
        return "^"
    if isinstance(pattern, EndPattern):
        return "$"
    if isinstance(pattern, RepeatPattern):
        body = _plain(pattern.body, syntax)
        if isinstance(pattern.body, (SeqPattern, AltPattern, RepeatPattern)):
            body = f"({body})"
        forms = {(0, 1): "?", (0, None): "*", (1, None): "+"}
        suffix = forms.get((pattern.min, pattern.max))
        if suffix is None and pattern.min == pattern.max:
            suffix = f"{{{pattern.min}}}"
        elif suffix is None and pattern.max is None:
            suffix = f"{{{pattern.min},}}"
        elif suffix is None and pattern.min == 0:
            suffix = f"{{,{pattern.max}}}"
        elif suffix is None:
            suffix = f"{{{pattern.min},{pattern.max}}}"
        return body + suffix
    if isinstance(pattern, SeqPattern):
        text = " ".join(
            f"({_plain(part, syntax)})"
            if isinstance(part, AltPattern)
            else _plain(part, syntax, 2)
            for part in pattern.parts
        )
        return f"({text})" if parent > _SEQUENCE_PRECEDENCE else text
    if isinstance(pattern, AltPattern):
        text = " | ".join(_plain(part, syntax, 1) for part in pattern.parts)
        return f"({text})" if parent > 1 else text
    raise ValueError(
        "a FocusPattern must be the whole pattern or a top-level sequence part"
    )


def format_pattern(pattern: Pattern, syntax: PredicateSyntax) -> str:
    """Return canonical text for one representable sequence pattern."""
    if isinstance(pattern, FocusPattern):
        target = _plain(pattern.body, syntax)
        if isinstance(pattern.body, AltPattern):
            target = f"({target})"
        return f"{target} / _"
    if isinstance(pattern, SeqPattern):
        focused = [
            (index, part)
            for index, part in enumerate(pattern.parts)
            if isinstance(part, FocusPattern)
        ]
        if len(focused) == 1:
            index, focus = focused[0]
            target = _plain(focus.body, syntax)
            if isinstance(focus.body, AltPattern):
                target = f"({target})"
            left = (
                _plain(_sequence(list(pattern.parts[:index])), syntax) if index else ""
            )
            if index == 1 and isinstance(pattern.parts[0], AltPattern):
                left = f"({left})"
            tail = pattern.parts[index + 1 :]
            right = _plain(_sequence(list(tail)), syntax) if tail else ""
            if len(tail) == 1 and isinstance(tail[0], AltPattern):
                right = f"({right})"
            context = (left + " _ " + right).strip()
            return f"{target} / {context}"
    return _plain(pattern, syntax)


__all__ = ["format_pattern", "parse_pattern", "parse_pattern_at"]
