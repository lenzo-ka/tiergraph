"""Text parsing and formatting for value predicates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import NoReturn

from tiergraph.core import QualifiedName, Refusal, RefusalStage
from tiergraph.predicate import (
    _BARE_FORBIDDEN,
    And,
    Bare,
    Cell,
    Compare,
    Current,
    Elements,
    Equals,
    Has,
    Literal,
    Matches,
    Not,
    Operand,
    Or,
    Order,
    Predicate,
    PredicateSyntax,
    Quantifier,
    _is_bare,
    _literal_key,
    _literal_text,
)

_MAX_PREDICATE_NESTING = 256
_SLOT_FORM_SIZE = 2


@dataclass(frozen=True, slots=True)
class _TextOption:
    value: Literal
    alias: bool
    offset: int
    spelling: str


class _PredicateParser:
    def __init__(
        self,
        text: str,
        start: int,
        end: int,
        syntax: PredicateSyntax,
    ) -> None:
        self.text = text
        self.index = start
        self.end = end
        self.syntax = syntax
        self.nesting = 0

    def parse(self) -> Predicate:
        """Parse exactly the configured source interval."""
        self.space()
        if self.index == self.end:
            self.refuse(self.index, "empty predicate; write a test such as class=vowel")
        result = self.expression()
        self.space()
        if self.index != self.end:
            self.unexpected()
        return result

    def expression(self, minimum: int = 1) -> Predicate:
        """Parse binary operators by precedence, flattening equal operators."""
        left = self.unary()
        while True:
            self.space()
            if self.text.startswith("||", self.index):
                self.refuse(
                    self.index,
                    "'||' is reserved for ordered choice; write '|' for or",
                )
            if self.text.startswith("&&", self.index):
                self.refuse(
                    self.index,
                    "'&&' is reserved for short-circuit conjunction; write '&' for and",
                )
            character = self.peek()
            precedence = {"|": 1, "&": 2}.get(character, 0)
            if precedence < minimum:
                break
            operator_offset = self.index
            self.index += 1
            self.space()
            if self.index == self.end or self.peek() == ")":
                message = (
                    "'|' has no value or test after it"
                    if character == "|"
                    else "unexpected ''"
                )
                self.refuse(operator_offset, message)
            right = self.expression(precedence + 1)
            left = (
                self.join_or([left, right])
                if character == "|"
                else self.join_and([left, right])
            )
        return left

    def unary(self) -> Predicate:
        """Parse prefix negation or one primary."""
        self.space()
        if self.take("!"):
            self.space()
            return Not(self.unary())
        return self.primary()

    def primary(self) -> Predicate:
        """Parse a group, quantified test, or ordinary test."""
        self.space()
        if self.peek() == "(":
            opening = self.index
            self.index += 1
            self.enter(opening)
            self.space()
            result = self.expression()
            self.space()
            if not self.take(")"):
                self.refuse(opening, "'(' is never closed")
            self.nesting -= 1
            return result
        quantifier = self.quantifier_ahead()
        if quantifier is not None:
            return self.quantified(quantifier)
        return self.test()

    def quantified(self, quantifier: Quantifier) -> Predicate:
        """Parse any/all/none over a JSON array operand."""
        self.index += len(quantifier.value)
        self.space()
        opening = self.index
        self.index += 1
        self.enter(opening)
        self.space()
        operand, _, _ = self.operand()
        self.space()
        if not self.take(":"):
            self.unexpected()
        self.space()
        body = self.expression()
        self.space()
        if not self.take(")"):
            self.refuse(opening, "'(' is never closed")
        self.nesting -= 1
        return Elements(operand, quantifier, body)

    def test(self) -> Predicate:
        """Parse one operand and its value-test operator."""
        operand, operand_start, operand_text = self.operand()
        self.space()
        operation = next(
            (
                candidate
                for candidate in ("!=", "<=", ">=", "=", "<", ">", "~")
                if self.text.startswith(candidate, self.index)
            ),
            None,
        )
        if operation is None:
            alias = (
                self.syntax.missing_aliases[0] if self.syntax.missing_aliases else None
            )
            hint = (
                f"write {operand_text}!={alias} to test that it is present"
                if alias is not None
                else f'write a test such as {operand_text}="x"'
            )
            self.refuse(
                operand_start,
                f"{operand_text!r} names a cell but tests nothing; {hint}",
            )
        operator_offset = self.index
        self.index += len(operation)
        self.space()
        if operation == "~":
            if self.peek() != '"':
                self.refuse(
                    operator_offset,
                    "'~' needs a quoted regular expression, as in label~\"a.*\"",
                )
            regex, positions = self.quoted()
            try:
                return Matches(operand, regex)
            except ValueError as error:
                prefix = "regex at offset "
                message = str(error)
                number, detail = message[len(prefix) :].split(": ", 1)
                relative = int(number)
                offset = (
                    positions[relative] if relative < len(positions) else self.index - 1
                )
                self.refuse(offset, f"regex: {detail}", error)
        if operation in {"<", "<=", ">", ">="}:
            option = self.option()
            if option.alias:
                self.refuse(
                    option.offset,
                    f"{option.spelling!r} is the missing-cell alias and has no order; "
                    "ordered comparison needs a value",
                )
            return Compare(operand, Order(operation), option.value)
        options = self.slot()
        self.check_vocabulary(operand, operand_text, options)
        equality = self.lower_equality(operand, options)
        if operation == "=":
            return equality
        if isinstance(equality, Not) and isinstance(equality.arg, Has):
            return equality.arg
        return Not(equality)

    def slot(self) -> list[_TextOption]:
        """Parse one equality value list, parenthesized or continued."""
        self.space()
        if self.peek() == "(":
            opening = self.index
            self.index += 1
            options = [self.option()]
            self.space()
            while self.take("|"):
                self.space()
                options.append(self.option())
                self.space()
            if not self.take(")"):
                self.refuse(opening, "'(' is never closed")
            if len(options) == 1:
                self.refuse(
                    opening,
                    "a parenthesized value list needs at least two values; "
                    "write the value without parentheses",
                )
        else:
            options = [self.option()]
            self.space()
            while self.peek() == "|" and self.choice_continues():
                self.index += 1
                self.space()
                options.append(self.option())
                self.space()
        self.check_duplicates(options)
        return options

    def option(self) -> _TextOption:
        """Parse one quoted or bare scalar, marking declared aliases."""
        self.space()
        start = self.index
        character = self.peek()
        if character == '"':
            value, _ = self.quoted()
            return _TextOption(value, False, start, value)
        if character == ".":
            self.refuse(start, "a value cannot start with '.'; write 0.5 for a number")
        if character == "∅":
            self.refuse(
                start,
                "'∅' is reserved for the empty language; write the missing-cell "
                f"alias {self.first_alias()} for a missing cell",
            )
        token = self.bare()
        return _TextOption(
            Bare(token), token in self.syntax.missing_aliases, start, token
        )

    def operand(self) -> tuple[Operand, int, str]:
        """Parse a cell or current operand and its pointer."""
        self.space()
        start = self.index
        if self.take("."):
            result: Operand = Current()
        else:
            first = self.bare(cell=True)
            prefix: str | None
            if (
                self.peek() == ":"
                and self.index + 1 < self.end
                and not self.text[self.index + 1].isspace()
                and self.text[self.index + 1] not in _BARE_FORBIDDEN
            ):
                self.index += 1
                prefix = first
                local = self.bare(cell=True)
            else:
                prefix = self.syntax.default_prefix
                local = first
                if prefix is None:
                    self.refuse(
                        start,
                        f"{first!r} has no prefix, and this syntax declares no "
                        "default prefix",
                    )
            namespace = next(
                (
                    binding.namespace
                    for binding in self.syntax.namespaces
                    if binding.prefix == prefix
                ),
                None,
            )
            if namespace is None:
                self.refuse(start, f"prefix {prefix!r} is not declared in this syntax")
            result = Cell(QualifiedName(namespace, local))
        pointer: list[str] = []
        while self.take("/"):
            if self.peek() == '"':
                key, _ = self.quoted()
            else:
                key = self.bare()
            pointer.append(key)
        if pointer:
            result = (
                Cell(result.attribute, tuple(pointer))
                if isinstance(result, Cell)
                else Current(tuple(pointer))
            )
        return result, start, self.text[start : self.index]

    def quoted(self) -> tuple[str, list[int]]:
        """Parse one uniformly quoted string and retain decoded source offsets."""
        opening = self.index
        self.index += 1
        characters: list[str] = []
        positions: list[int] = []
        while self.index < self.end:
            character = self.text[self.index]
            if character == '"':
                self.index += 1
                return "".join(characters), positions
            if character == "\\":
                escape = self.index
                self.index += 1
                if self.index >= self.end:
                    self.refuse(opening, "quoted string is never closed")
                escaped = self.text[self.index]
                if escaped not in {'"', "\\"}:
                    self.refuse(
                        escape,
                        f"'\\{escaped}' is not an escape; a quoted string admits "
                        'only \\" and \\\\',
                    )
                character = escaped
                positions.append(escape)
            else:
                positions.append(self.index)
            characters.append(character)
            self.index += 1
        self.refuse(opening, "quoted string is never closed")

    def bare(self, *, cell: bool = False) -> str:
        """Parse one bare token or issue its targeted lexical refusal."""
        start = self.index
        if self.peek() == ".":
            if cell:
                self.refuse(start, "a cell name cannot start with '.'")
            self.unexpected()
        while self.index < self.end:
            character = self.text[self.index]
            if character.isspace() or character in _BARE_FORBIDDEN:
                break
            self.index += 1
        if self.index > start:
            return self.text[start : self.index]
        self.unexpected()

    def choice_continues(self) -> bool:
        """Return whether the bar at the cursor continues an equality slot."""
        if self.text.startswith("||", self.index):
            return False
        cursor = self.index + 1
        while cursor < self.end and self.text[cursor].isspace():
            cursor += 1
        if cursor >= self.end:
            return False
        if self.text[cursor] == '"':
            cursor += 1
            while cursor < self.end:
                if self.text[cursor] == "\\":
                    cursor += 2
                    continue
                if self.text[cursor] == '"':
                    cursor += 1
                    break
                cursor += 1
        else:
            beginning = cursor
            while cursor < self.end:
                character = self.text[cursor]
                if character.isspace() or character in _BARE_FORBIDDEN:
                    break
                cursor += 1
            if cursor == beginning:
                return False
        while cursor < self.end and self.text[cursor].isspace():
            cursor += 1
        return cursor >= self.end or self.text[cursor] not in "=<>~/:("

    def check_duplicates(self, options: list[_TextOption]) -> None:
        """Refuse repeated aliases and literals at the second spelling."""
        aliases: set[str] = set()
        literals: set[tuple[type[object], object]] = set()
        for option in options:
            if option.alias:
                if option.spelling in aliases:
                    self.refuse(
                        option.offset,
                        f"the missing-cell alias {option.spelling!r} appears twice "
                        "in one value list",
                    )
                aliases.add(option.spelling)
            else:
                key = _literal_key(option.value)
                if key in literals:
                    self.refuse(
                        option.offset,
                        f"{option.spelling!r} appears twice in one value list",
                    )
                literals.add(key)

    def check_vocabulary(
        self,
        operand: Operand,
        operand_text: str,
        options: list[_TextOption],
    ) -> None:
        """Refuse an alias declared as a value of the tested attribute."""
        if not isinstance(operand, Cell):
            return
        vocabulary = next(
            (
                values
                for name, values in self.syntax.vocabularies
                if name == operand.attribute
            ),
            (),
        )
        for option in options:
            if option.alias and option.spelling in vocabulary:
                alias = option.spelling
                self.refuse(
                    option.offset,
                    f"{operand_text}={alias}: {alias!r} is both the missing-cell "
                    f"alias and a declared value of {operand_text}; write "
                    f"{operand_text}={json.dumps(alias)} for the value, or declare "
                    "a different missing alias",
                )

    @staticmethod
    def lower_equality(operand: Operand, options: list[_TextOption]) -> Predicate:
        """Lower one parsed equality slot to the S1 predicate AST."""
        values = tuple(option.value for option in options if not option.alias)
        parts: list[Predicate] = []
        if values:
            parts.append(Equals(operand, values))
        parts.extend(
            Not(Has(operand, alias=option.spelling))
            for option in options
            if option.alias
        )
        if len(parts) == 1:
            return parts[0]
        return Or(tuple(parts))

    def quantifier_ahead(self) -> Quantifier | None:
        """Return a quantified keyword only when an opening parenthesis follows."""
        for quantifier in Quantifier:
            if not self.text.startswith(quantifier.value, self.index):
                continue
            cursor = self.index + len(quantifier.value)
            if cursor < self.end and (
                not self.text[cursor].isspace() and self.text[cursor] != "("
            ):
                continue
            while cursor < self.end and self.text[cursor].isspace():
                cursor += 1
            if cursor < self.end and self.text[cursor] == "(":
                return quantifier
        return None

    @staticmethod
    def join_and(arguments: list[Predicate]) -> Predicate:
        """Flatten syntactic conjunction groups."""
        flattened = tuple(
            child
            for argument in arguments
            for child in (argument.args if isinstance(argument, And) else (argument,))
        )
        return flattened[0] if len(flattened) == 1 else And(flattened)

    @staticmethod
    def join_or(arguments: list[Predicate]) -> Predicate:
        """Flatten syntactic disjunction groups."""
        flattened = tuple(
            child
            for argument in arguments
            for child in (argument.args if isinstance(argument, Or) else (argument,))
        )
        return flattened[0] if len(flattened) == 1 else Or(flattened)

    def enter(self, offset: int) -> None:
        """Enter one predicate group under the declared nesting limit."""
        self.nesting += 1
        if self.nesting > _MAX_PREDICATE_NESTING:
            self.refuse(offset, "predicate nests deeper than 256")

    def first_alias(self) -> str:
        """Return the spelling used by diagnostics that need one alias."""
        return self.syntax.missing_aliases[0] if self.syntax.missing_aliases else "none"

    def space(self) -> None:
        """Consume insignificant spacing."""
        while self.index < self.end and self.text[self.index].isspace():
            self.index += 1

    def peek(self) -> str:
        """Return the current character or an empty end marker."""
        return self.text[self.index] if self.index < self.end else ""

    def take(self, token: str) -> bool:
        """Consume a token inside the configured interval."""
        if self.text.startswith(token, self.index, self.end):
            self.index += len(token)
            return True
        return False

    def unexpected(self) -> NoReturn:
        """Issue a targeted or generic refusal at the cursor."""
        offset = self.index
        if self.text.startswith("||", offset):
            self.refuse(offset, "'||' is reserved for ordered choice; write '|' for or")
        if self.text.startswith("&&", offset):
            self.refuse(
                offset,
                "'&&' is reserved for short-circuit conjunction; write '&' for and",
            )
        character = self.peek()
        if character == ")":
            self.refuse(offset, "')' closes no group")
        if character == "?":
            self.refuse(offset, "'?' calls a declared predicate, which is reserved")
        if character == "%":
            self.refuse(offset, "'%' is reserved for typed literals")
        if character == "∅":
            self.refuse(
                offset,
                "'∅' is reserved for the empty language; write the missing-cell "
                f"alias {self.first_alias()} for a missing cell",
            )
        if character == "|":
            self.refuse(offset, "'|' has no value or test after it")
        self.refuse(offset, f"unexpected {character!r}")

    @staticmethod
    def refuse(
        offset: int,
        message: str,
        cause: Exception | None = None,
    ) -> NoReturn:
        """Raise one source-located value-test syntax refusal."""
        error = Refusal(RefusalStage.SYNTAX, f"predicate at offset {offset}: {message}")
        if cause is None:
            raise error
        raise error from cause


def parse_predicate(text: str, syntax: PredicateSyntax) -> Predicate:
    """Parse one complete value-test predicate."""
    return _PredicateParser(text, 0, len(text), syntax).parse()


def parse_predicate_at(
    text: str,
    start: int,
    syntax: PredicateSyntax,
    terminator: str = "}",
) -> tuple[Predicate, int]:
    """Parse until one requested terminator outside quotes and parentheses."""
    if terminator not in {"}", "]", ")", ","}:
        raise ValueError(
            f"terminator {terminator!r} is value-test notation; choose one of }} ] ) ,"
        )
    if start < 0 or start > len(text):
        raise ValueError(f"predicate start {start} is outside the text")
    depth = 0
    quoted = False
    escaped = False
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
        elif character == '"':
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        elif character == terminator and depth == 0:
            return _PredicateParser(text, start, cursor, syntax).parse(), cursor
        cursor += 1
    raise Refusal(
        RefusalStage.SYNTAX,
        f"predicate at offset {len(text)}: predicate text has no closing {terminator!r}",
    )


def _quoted_text(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_name(name: QualifiedName, syntax: PredicateSyntax) -> str:
    binding = next(
        (item for item in syntax.namespaces if item.namespace == name.namespace), None
    )
    if binding is None:
        raise ValueError(f"namespace {name.namespace!r} is not declared in this syntax")
    if not _is_bare(name.local_name):
        raise ValueError(f"local name {name.local_name!r} has no value-test text form")
    if binding.prefix == syntax.default_prefix:
        return name.local_name
    return f"{binding.prefix}:{name.local_name}"


def _format_operand(operand: Operand, syntax: PredicateSyntax) -> str:
    root = _format_name(operand.attribute, syntax) if isinstance(operand, Cell) else "."
    keys = "".join(
        "/" + (key if _is_bare(key) else _quoted_text(key)) for key in operand.pointer
    )
    return root + keys


def _format_literal(value: Literal, syntax: PredicateSyntax) -> str:
    if isinstance(value, Bare):
        if value.text in syntax.missing_aliases:
            raise ValueError(
                f"Bare({value.text!r}) is spelled like the missing-cell alias; "
                "it has no text form under this syntax"
            )
        return value.text
    if type(value) is str:
        return _quoted_text(value)
    raise ValueError(
        f"literal {_literal_text(value)} ({type(value).__name__}) has no text form; "
        "bare text is typed by the cell at bind, so write "
        f'Bare("{_literal_text(value)}") or use JSON'
    )


def _require_alias(alias: str, syntax: PredicateSyntax) -> str:
    if alias not in syntax.missing_aliases:
        raise ValueError(f"alias {alias!r} is not declared in this syntax")
    return alias


def _slot_form(
    predicate: Predicate,
) -> tuple[Operand, tuple[Literal, ...], str | None] | None:
    if isinstance(predicate, Equals):
        return predicate.operand, predicate.values, None
    if (
        isinstance(predicate, Or)
        and len(predicate.args) == _SLOT_FORM_SIZE
        and isinstance(predicate.args[0], Equals)
        and isinstance(predicate.args[1], Not)
        and isinstance(predicate.args[1].arg, Has)
        and predicate.args[0].operand == predicate.args[1].arg.operand
    ):
        equality = predicate.args[0]
        presence = predicate.args[1].arg
        return equality.operand, equality.values, presence.alias
    return None


def _format_predicate(
    predicate: Predicate, syntax: PredicateSyntax, parent: int
) -> str:
    slot = _slot_form(predicate)
    if slot is not None:
        operand, values, alias = slot
        parts = [_format_literal(value, syntax) for value in values]
        if alias is not None:
            parts.append(_require_alias(alias, syntax))
        return f"{_format_operand(operand, syntax)}={'|'.join(parts)}"
    if isinstance(predicate, Has):
        return (
            f"{_format_operand(predicate.operand, syntax)}!="
            f"{_require_alias(predicate.alias, syntax)}"
        )
    if isinstance(predicate, Compare):
        return (
            f"{_format_operand(predicate.operand, syntax)}{predicate.order.value}"
            f"{_format_literal(predicate.value, syntax)}"
        )
    if isinstance(predicate, Matches):
        return f"{_format_operand(predicate.operand, syntax)}~{_quoted_text(predicate.regex)}"
    if isinstance(predicate, Elements):
        return (
            f"{predicate.quantifier.value}("
            f"{_format_operand(predicate.operand, syntax)}: "
            f"{_format_predicate(predicate.body, syntax, 0)})"
        )
    if isinstance(predicate, Not):
        if isinstance(predicate.arg, Has):
            return (
                f"{_format_operand(predicate.arg.operand, syntax)}="
                f"{_require_alias(predicate.arg.alias, syntax)}"
            )
        negated_slot = _slot_form(predicate.arg)
        if negated_slot is not None:
            operand, values, alias = negated_slot
            parts = [_format_literal(value, syntax) for value in values]
            if alias is not None:
                parts.append(_require_alias(alias, syntax))
            return f"{_format_operand(operand, syntax)}!={'|'.join(parts)}"
        child = _format_predicate(predicate.arg, syntax, 0)
        if isinstance(predicate.arg, And | Or):
            child = f"({child})"
        return "!" + child
    if isinstance(predicate, And | Or):
        if not predicate.args:
            raise ValueError(
                f"{type(predicate).__name__} has no value-test text; use JSON"
            )
        precedence = 2 if isinstance(predicate, And) else 1
        separator = " & " if isinstance(predicate, And) else " | "
        text = separator.join(
            _format_predicate(argument, syntax, precedence)
            for argument in predicate.args
        )
        return f"({text})" if precedence < parent else text
    raise ValueError(f"{type(predicate).__name__} has no value-test text; use JSON")


def format_predicate(predicate: Predicate, syntax: PredicateSyntax) -> str:
    """Return the canonical value-test text for a representable predicate."""
    return _format_predicate(predicate, syntax, 0)


__all__ = ["format_predicate", "parse_predicate", "parse_predicate_at"]
