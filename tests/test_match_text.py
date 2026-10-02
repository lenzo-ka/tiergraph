"""Text syntax for regular sequence patterns."""

from __future__ import annotations

import json
import re

import pytest

from tiergraph import NamespaceDeclaration, QualifiedName, Refusal, RefusalStage
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
    compile_pattern,
    format_pattern,
    parse_pattern,
    parse_pattern_at,
    pattern_loads,
    pattern_to_data,
)
from tiergraph.predicate import (
    And,
    Bare,
    Cell,
    Current,
    Equals,
    PredicateSyntax,
    Quantifier,
    Related,
)
from tiergraph.traversal import WalkDirection

NS = "urn:example:fixture#"
SYN = PredicateSyntax((NamespaceDeclaration("ex", NS),), "ex")


def q(local: str) -> QualifiedName:
    return QualifiedName(NS, local)


def atom(value: str) -> AtomPattern:
    return AtomPattern(Equals(Cell(q("seg")), (Bare(value),)))


def parsed(text: str) -> Pattern:
    return parse_pattern(text, SYN)


def test_quantifiers_any_item_and_precedence() -> None:
    a, b, c = atom("a"), atom("b"), atom("c")
    assert parsed("{seg=a}*") == parsed("({seg=a})*") == RepeatPattern(a, 0, None)
    forms = {
        "+": (1, None),
        "?": (0, 1),
        "{2}": (2, 2),
        "{2,}": (2, None),
        "{,3}": (0, 3),
        "{ 2 , 3 }": (2, 3),
    }
    for suffix, bounds in forms.items():
        assert parsed("{seg=a}" + suffix) == RepeatPattern(a, *bounds)
    any_item = AtomPattern(And(()))
    assert parsed(".") == any_item
    assert parsed(".*") == RepeatPattern(any_item, 0, None)
    assert parsed("{.=ten} . {.=five}") == SeqPattern(
        (
            AtomPattern(Equals(Current(), (Bare("ten"),))),
            any_item,
            AtomPattern(Equals(Current(), (Bare("five"),))),
        )
    )

    assert parsed("({seg=a} {seg=b})*") == RepeatPattern(SeqPattern((a, b)), 0, None)
    assert parsed("({seg=a} | {seg=b} {seg=c})?") == RepeatPattern(
        AltPattern((a, SeqPattern((b, c)))), 0, 1
    )
    assert parsed("{seg=a} ({seg=b} {seg=c})") == SeqPattern((a, b, c))


def test_focus_context_lowering_and_grouped_alternatives() -> None:
    vowel = AtomPattern(Equals(Cell(q("class")), (Bare("vowel"),)))
    nasal = AtomPattern(Equals(Cell(q("class")), (Bare("nasal"),)))
    assert parsed("{class=vowel} / _ {class=nasal}") == SeqPattern(
        (FocusPattern(vowel), nasal)
    )
    assert parsed("{class=vowel} / ^ _") == SeqPattern(
        (StartPattern(), FocusPattern(vowel))
    )
    assert parsed("{class=vowel} / _ $") == SeqPattern(
        (FocusPattern(vowel), EndPattern())
    )
    assert parsed("({seg=a} | {seg=b}) / {seg=x} _") == SeqPattern(
        (atom("x"), FocusPattern(AltPattern((atom("a"), atom("b")))))
    )


@pytest.mark.parametrize(
    ("text", "offset", "message"),
    [
        ("", 0, "empty pattern"),
        ("{}", 0, "empty item test"),
        ("{2}", 0, "nothing before it"),
        ("*", 0, "nothing before it"),
        ("{seg=a} *", 8, "follows a space"),
        ("{seg=a}**", 8, "stacks a second quantifier"),
        ("{seg=a}*?", 7, "lazy quantifier"),
        ("{seg=a}{2,,3}", 7, "not a counted quantifier"),
        ("{seg=a}{3,2}", 7, "minimum 3 greater"),
        ("{seg=a}{0}", 7, "matches only the empty"),
        ("{seg=a}{10001}", 7, "exceeds limit 10000"),
        ("{seg=a} {2}", 8, "follows a space"),
        ("({seg=a})", 0, "groups one element"),
        ("()", 0, "empty group"),
        ("({seg=a}", 0, "never closed"),
        ("{seg=a}|", 7, "empty alternative"),
        ("{seg=a}||{seg=b}", 7, "reserved for ordered choice"),
        ("&&", 0, "short-circuit conjunction"),
        ("(?i:{seg=a})", 2, "flag 'i' is not defined"),
        ("(?)", 0, "sets no flag"),
        ("(?-:{seg=a})", 2, "clears no flag"),
        ("(?ii:{seg=a})", 3, "repeated in '(?ii:'"),
        ("(?i-i:{seg=a})", 4, "both set and cleared in '(?i-i:'"),
        ("(?={seg=a})", 0, "not a flag group"),
        ("#", 0, "not pattern notation"),
        ("[", 0, "is reserved"),
        ("word", 0, "is a bare word"),
        ('"a"', 0, "quoted string"),
        ("&", 0, "conjoins item tests"),
        ("!", 0, "negates item tests"),
        ("{seg=a}|{seg=b} / _", 16, "alternatives before"),
        ("{seg=a} / {seg=x}|{seg=y} _", 26, "alternatives in a context"),
        ("{seg=a}/", 7, "needs exactly one"),
        ("_", 0, "belongs after '/'"),
        ("{seg=a} _", 8, "belongs after '/'"),
        ("{seg=a})", 7, "closes no group"),
        ("{seg=a} // _", 9, "at most one '/'"),
        ("{seg=a} / {seg=x} / _", 18, "at most one '/'"),
        ("{seg=a} / {seg=x}", 8, "needs exactly one"),
        ("{seg=a} / _ {seg=x}|{seg=y}", 27, "alternatives in a context"),
        ("{seg=a} / _ {seg=x} /", 20, "at most one '/'"),
        ("{seg=a} / _ {seg=x} _", 8, "needs exactly one"),
        ("{seg=a} / _ {seg=x})", 19, "closes no group"),
        ("{seg=a}/_ /", 10, "at most one '/'"),
        ("(_)", 1, "cannot be grouped"),
        ("({seg=a} _)", 9, "cannot be grouped"),
        ("^ / _", 2, "focus can match no item"),
        ("^*", 1, "inside a repetition"),
        ("$*", 1, "'$' is inside a repetition"),
        ("({seg=a} ^)*", 11, "'^' is inside a repetition"),
        ("(^ | $)*", 7, "'^' is inside a repetition"),
        ("^ {seg=a} / _", 10, "'^' is inside the focus"),
        ("$ {seg=a} / _", 10, "'$' is inside the focus"),
        ("(?$:{seg=a})", 0, "not a flag group"),
        ("%", 0, "is reserved"),
        (",", 0, "unexpected ','"),
        ("=", 0, "unexpected '='"),
        ("\\", 0, "escapes nothing"),
        ("∅", 0, "empty language"),
        ("]", 0, "unexpected ']'"),
        ("@", 0, "is reserved"),
        (")", 0, "closes no group"),
    ],
)
def test_pattern_refusals_are_source_located(
    text: str, offset: int, message: str
) -> None:
    with pytest.raises(Refusal, match=re.escape(message)) as caught:
        parsed(text)
    assert caught.value.stage is RefusalStage.SYNTAX
    assert str(caught.value).startswith(f"pattern at offset {offset}:")


def test_format_json_and_text_round_trips() -> None:
    texts = (
        "{seg=a}?",
        "{seg=a}{1}",
        "{seg=a}{,3}",
        "({seg=a} {seg=b})+",
        "({seg=a}{2})*",
        "({seg=a} | {seg=b}) {seg=c}",
        "{class=vowel} / ^ _ $",
        "(?:{seg=a} {seg=b})*",
        '{seg="\\"}"} {seg=""}',
    )
    for text in texts:
        pattern = parse_pattern(text, SYN)
        canonical = format_pattern(pattern, SYN)
        assert parse_pattern(canonical, SYN) == pattern
        assert format_pattern(parse_pattern(canonical, SYN), SYN) == canonical
        assert pattern_loads(json.dumps(pattern_to_data(pattern))) == pattern

    related = Related(q("rel"), WalkDirection.FORWARD, Quantifier.ANY, And(()))
    with pytest.raises(ValueError, match="pattern atom has no text form: Related"):
        format_pattern(AtomPattern(related), SYN)


def test_formatter_covers_canonical_context_shapes() -> None:
    a, b, c = atom("a"), atom("b"), atom("c")
    alternatives = AltPattern((a, b))
    assert format_pattern(AtomPattern(And(())), SYN) == "."
    assert format_pattern(RepeatPattern(a, 2, None), SYN) == "{seg=a}{2,}"
    assert format_pattern(RepeatPattern(a, 2, 3), SYN) == "{seg=a}{2,3}"
    assert format_pattern(FocusPattern(alternatives), SYN) == (
        "({seg=a} | {seg=b}) / _"
    )
    assert format_pattern(FocusPattern(a), SYN) == "{seg=a} / _"
    assert format_pattern(SeqPattern((FocusPattern(alternatives), c)), SYN) == (
        "({seg=a} | {seg=b}) / _ {seg=c}"
    )
    assert format_pattern(SeqPattern((alternatives, FocusPattern(c))), SYN) == (
        "{seg=c} / ({seg=a} | {seg=b}) _"
    )
    assert format_pattern(SeqPattern((FocusPattern(c), alternatives)), SYN) == (
        "{seg=c} / _ ({seg=a} | {seg=b})"
    )
    with pytest.raises(ValueError, match="must be the whole pattern"):
        format_pattern(AltPattern((FocusPattern(a), b)), SYN)

    foreign = QualifiedName("urn:example:foreign#", "seg")
    with pytest.raises(ValueError, match="namespace"):
        format_pattern(AtomPattern(Equals(Cell(foreign), (Bare("a"),))), SYN)


def test_parse_pattern_at_skips_quotes_braces_and_nested_groups() -> None:
    text = 'match({seg=a} / _ {seg=")"}) rest'
    pattern, end = parse_pattern_at(text, 6, SYN, ")")
    assert end == 27
    assert pattern == SeqPattern(
        (FocusPattern(atom("a")), AtomPattern(Equals(Cell(q("seg")), (")",))))
    )
    assert parse_pattern_at("match(({seg=a})+) x", 6, SYN, ")") == (
        RepeatPattern(atom("a"), 1, None),
        16,
    )
    escaped = 'match({seg="a\\")b"}) rest'
    assert parse_pattern_at(escaped, 6, SYN, ")")[1] == 19
    assert parse_pattern_at(".", 0, SYN) == (AtomPattern(And(())), 1)
    with pytest.raises(Refusal, match="no closing"):
        parse_pattern_at("match({seg=a}", 6, SYN, ")")
    with pytest.raises(ValueError, match="pattern notation"):
        parse_pattern_at(".", 0, SYN, "|")


def test_alias_empty_string_flags_and_limits() -> None:
    missing = parse_pattern("{stress=none}", SYN)
    assert format_pattern(missing, SYN) == "{stress=none}"
    empty = parse_pattern('{surface=""}', SYN)
    assert format_pattern(empty, SYN) == '{surface=""}'
    with pytest.raises(Refusal, match="empty language"):
        parse_pattern("{stress=∅}", SYN)
    assert parsed("(?:{seg=a} {seg=b})*") == RepeatPattern(
        SeqPattern((atom("a"), atom("b"))), 0, None
    )
    assert MAX_REPEAT_COUNT == 10_000
    compile_pattern(parsed("((.){100}){1000}"))
    with pytest.raises(Refusal, match="100100 item positions"):
        compile_pattern(parsed("((.){100}){1001}"))


def test_pattern_entry_point_argument_checks() -> None:
    with pytest.raises(Refusal, match="no closing"):
        parsed("{2")
    with pytest.raises(ValueError, match="outside the text"):
        parse_pattern_at(".", -1, SYN)
    with pytest.raises(ValueError, match="pattern notation"):
        parse_pattern_at(".", 0, SYN, " ")


def test_focus_separator_ignores_quoted_escaped_alternation() -> None:
    assert parsed('{seg="a\\"|b"} / _') == FocusPattern(
        AtomPattern(Equals(Cell(q("seg")), ('a"|b',)))
    )
