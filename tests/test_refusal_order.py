"""One declared total order governs every document reader's refusals."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

import tiergraph
from tests.test_wire import rich_graph
from tiergraph import (
    FORMAT_VERSION,
    AddItem,
    DeclareNamespace,
    DeclareTier,
    ExecutionError,
    Graph,
    GraphValidationError,
    NamespaceDeclaration,
    Program,
    QualifiedName,
    TierDeclaration,
    core,
    execute,
    loads,
    machine_codec,
    program_dumps,
    program_loads,
    schema,
    selection_loads,
    to_data,
    wire,
)
from tiergraph.schema import Refusal, RefusalStage, json_schema, validation_errors


def document() -> dict[str, Any]:
    """Return independent JSON-shaped document data this reader accepts."""
    return cast(dict[str, Any], json.loads(json.dumps(to_data(rich_graph()))))


def graph_of(value: dict[str, Any]) -> dict[str, Any]:
    """Return the graph member of a document under construction."""
    return cast(dict[str, Any], value["graph"])


def refuse(operation: object, *args: object) -> Refusal:
    """Run a reader that must refuse and return its refusal."""
    with pytest.raises(Refusal) as caught:
        operation(*args)  # type: ignore[operator]
    return caught.value


def test_the_declared_order_is_numbered_and_total() -> None:
    """CHARACTERIZATION: the order is one contiguous rank with no ties.

    A total order is what lets two co-applicable conditions be compared at all,
    so a duplicate or a gap in the numbering would leave a pair the declaration
    cannot rank.
    """
    numbers = [stage.value for stage in RefusalStage]
    assert numbers == list(range(1, len(numbers) + 1))
    assert len(set(numbers)) == len(numbers)
    assert RefusalStage.ENVELOPE < RefusalStage.ENCODING < RefusalStage.SYNTAX
    assert RefusalStage.SYNTAX < RefusalStage.CONSTRUCTION
    assert RefusalStage.CONSTRUCTION < RefusalStage.DISCRIMINATOR
    assert RefusalStage.DISCRIMINATOR < RefusalStage.SHAPE < RefusalStage.VALUE
    assert RefusalStage.VALUE < RefusalStage.REFERENCE < RefusalStage.SEMANTICS


def test_a_refusal_stays_a_value_error() -> None:
    """REGRESSION: staging a refusal does not move it out of anyone's except.

    Every reader in this package refused with `ValueError` before the stage
    existed, so the staged refusal must remain one or the change would be a
    silent break for every caller that already catches it.
    """
    assert issubclass(Refusal, ValueError)
    assert issubclass(ExecutionError, Refusal)
    refusal = refuse(loads, "{}")
    assert isinstance(refusal, ValueError)
    assert refusal.also == ()


def test_four_simultaneous_problems_report_the_first_and_the_rest() -> None:
    """REGRESSION: one attempt names every applicable condition, in order.

    The document below carries four independent problems in four subtrees, of
    three different classes.  A fixture with one problem cannot tell an order
    from an accident, so the discrimination is that the reported sequence
    follows the declaration outside in rather than the order the checks happen
    to run in, and that no problem after the first is dropped.
    """
    value = document()
    graph = graph_of(value)
    graph["tiers"][0]["declaration"]["long_name"] = 7
    graph["tiers"][1]["items"][0]["zz"] = 1
    graph["relations"][0]["left"]["index"] = -1
    graph["attribute_declarations"][0]["domain"] = "nope"

    assert validation_errors(value, FORMAT_VERSION) == [
        "document.graph.tiers[0].declaration.long_name must be a string",
        "document.graph.tiers[1].items[0] has unknown fields ['zz']",
        "document.graph.relations[0].left.index must be at least 0",
        "document.graph.attribute_declarations[0].domain has unsupported value 'nope'",
    ]

    refusal = refuse(loads, json.dumps(value))
    assert refusal.stage is RefusalStage.CONSTRUCTION
    assert str(refusal) == "tiers[0].declaration.long_name must be a string"


def test_a_foreign_version_suppresses_the_conditions_it_explains() -> None:
    """REGRESSION: the version is primary and nothing unjudgeable rides along.

    The same document also lacks `graph` and carries an unknown member, and
    both are conditions of the very node the version governs.  Under a
    declaration this release does not implement neither is judgeable, so the
    version is reported alone rather than beside a field set measured against
    the wrong declaration.
    """
    value = document()
    value["format_version"] = "7"
    value["aa"] = 1
    del value["graph"]

    refusal = refuse(loads, json.dumps(value))
    assert refusal.stage is RefusalStage.DISCRIMINATOR
    assert str(refusal) == "format_version '7' is unsupported; expected '0.2.0'"
    assert refusal.also == ()
    assert validation_errors(value, FORMAT_VERSION) == [
        "format_version '7' is unsupported; expected '0.2.0'"
    ]


def test_both_field_set_directions_are_carried_as_data() -> None:
    """REGRESSION: the second direction of a field set is readable without prose.

    A node missing members and carrying unknown ones meets two conditions of
    one node.  The combined message stays what it was, and the unknown-field
    condition is also a refusal on `also`, so a caller reads the second
    condition and its stage instead of parsing the sentence.
    """
    value = document()
    del value["graph"]
    value["aa"] = 1
    value["zz"] = 2

    refusal = refuse(loads, json.dumps(value))
    assert refusal.stage is RefusalStage.SHAPE
    assert str(refusal) == (
        "document is missing fields ['graph'] and has unknown fields ['aa', 'zz']"
    )
    assert [(entry.stage, str(entry)) for entry in refusal.also] == [
        (RefusalStage.SHAPE, "document has unknown fields ['aa', 'zz']")
    ]


def test_a_machine_header_reports_its_version_before_its_field_set() -> None:
    """REGRESSION: the program reader follows the same order as the document one.

    A header from a later release is a later version carrying new header
    members, so checking the header's field set first suppressed the one
    diagnostic that tells the caller the truth.  The stamp is decided first.
    """
    refusal = refuse(program_loads, b'{"machine_version":"2","extra":true}\n')
    assert refusal.stage is RefusalStage.DISCRIMINATOR
    assert str(refusal) == "header machine_version must be '1'"


def test_an_unstamped_machine_header_is_a_discriminator_condition() -> None:
    """REGRESSION: an absent stamp is reported as the stamp, not as a field set.

    Until the stamp is read the reader does not know which header shape
    applies, so it cannot honestly report a difference against this release's
    shape.
    """
    refusal = refuse(program_loads, b"{}\n")
    assert refusal.stage is RefusalStage.DISCRIMINATOR
    assert str(refusal) == "header is missing field 'machine_version'"


def test_a_stamped_machine_header_is_still_held_to_its_field_set() -> None:
    """CHARACTERIZATION: deciding the version first does not admit new members.

    Reading the stamp before the field set changes which condition is primary,
    never whether an unknown header member is refused.
    """
    refusal = refuse(program_loads, b'{"machine_version":"1","extra":true}\n')
    assert refusal.stage is RefusalStage.SHAPE
    assert str(refusal) == (
        "header fields must be ['machine_version']; got ['extra', 'machine_version']"
    )


def test_a_program_line_that_is_not_text_is_an_encoding_condition() -> None:
    """REGRESSION: bad bytes outrank bad syntax in the program reader too.

    The order separates "the bytes are text" from "the text is JSON", and a
    line that is not UTF-8 has no JSON to be wrong about.  Handing the
    undecoded line to the JSON parser reported the condition one rank late and
    under that parser's own encoding guess: a line opening with a UTF-16
    byte-order mark was read as UTF-16 rather than refused as not UTF-8.  The
    offending line follows the header so the reported number is shown to be
    the line the bytes are on rather than a constant.
    """
    refusal = refuse(program_loads, b'{"machine_version":"1"}\n\xff\n')
    assert refusal.stage is RefusalStage.ENCODING
    assert str(refusal) == "JSONL line 2: parse UTF-8 failed: invalid start byte"
    assert refusal.also == ()


def test_a_program_in_a_foreign_encoding_is_refused_rather_than_read() -> None:
    """REGRESSION: the reader decodes UTF-8 instead of guessing an encoding.

    Handing an undecoded line to `json.loads` let that function sniff an
    encoding from the leading bytes, so a program written in UTF-16BE or
    UTF-32BE was not mis-staged but *read*: it came back as an equal `Program`
    carrying every opcode and a matching fingerprint, built from bytes the
    format does not admit.  Only the big-endian encodings reached that far,
    because they place the newline byte last and so survive splitting on it,
    which is why this uses a multi-line program rather than a bare header --
    a single-line fixture would show a wrong success but not that the whole
    program was reconstructed from it.

    A condition reported at the wrong rank is a diagnostic defect.  Accepting
    a document in an encoding the format does not admit is a soundness one, so
    the assertion here is that nothing is returned at all.
    """
    namespace = "urn:refusal"
    tier = QualifiedName(namespace, "events")
    program = Program(
        (
            DeclareNamespace(NamespaceDeclaration("c", namespace)),
            DeclareTier(TierDeclaration(tier, "Events")),
            AddItem(tier),
        )
    )
    canonical = program_dumps(program)
    assert program_loads(canonical.encode("utf-8")) == program

    for encoding in ("utf-16-be", "utf-32-be", "utf-16-le", "utf-32-le", "utf-8-sig"):
        refusal = refuse(program_loads, canonical.encode(encoding))
        assert refusal.stage in {RefusalStage.ENCODING, RefusalStage.SYNTAX}
        assert str(refusal).startswith("JSONL line 1: ")


def test_a_program_line_with_a_repeated_key_is_a_syntax_condition() -> None:
    """REGRESSION: an ambiguous object is refused rather than resolved.

    A repeated key is a syntax condition for the other three readers, and the
    program reader parsed without the hook that raises it, so the JSON
    library's last-wins rule silently chose a reading the input does not
    determine.  The header case would otherwise be accepted outright, and the
    opcode case would otherwise be reported as whatever the surviving member
    made of the record, which is a later stage judging a text that was never
    settled.
    """
    header = refuse(program_loads, b'{"machine_version":"1","machine_version":"1"}\n')
    assert header.stage is RefusalStage.SYNTAX
    assert str(header) == (
        "JSONL line 1: parse JSON failed: duplicate object key 'machine_version'"
    )

    opcode = refuse(program_loads, b'{"machine_version":"1"}\n{"a":1,"a":2}\n')
    assert opcode.stage is RefusalStage.SYNTAX
    assert str(opcode) == "JSONL line 2: parse JSON failed: duplicate object key 'a'"


def tier(name: str) -> str:
    """Return a one-tier document naming its tier however the caller spells it."""
    return json.dumps(
        {
            "format_version": FORMAT_VERSION,
            "graph": {"tiers": [{"declaration": {"name": name, "long_name": "X"}}]},
        }
    )


DOCUMENT_STAGES: tuple[tuple[str | bytes, RefusalStage], ...] = (
    ("x" * (wire.MAX_DOCUMENT_BYTES + 1), RefusalStage.ENVELOPE),
    (b"\xff\xfe", RefusalStage.ENCODING),
    ("{", RefusalStage.SYNTAX),
    ("[]", RefusalStage.CONSTRUCTION),
    ('{"graph":{}}', RefusalStage.DISCRIMINATOR),
    ('{"format_version":"0.2.0","graph":{},"zz":1}', RefusalStage.SHAPE),
    (tier("unqualified"), RefusalStage.VALUE),
    (tier("p:words"), RefusalStage.REFERENCE),
    (
        json.dumps(
            {
                "format_version": FORMAT_VERSION,
                "graph": {
                    "namespaces": [
                        {"prefix": "p", "namespace": "urn:a"},
                        {"prefix": "p", "namespace": "urn:b"},
                    ]
                },
            }
        ),
        RefusalStage.SEMANTICS,
    ),
)


@pytest.mark.parametrize(("source", "stage"), DOCUMENT_STAGES)
def test_each_document_reading_stage_is_reachable(
    source: str | bytes, stage: RefusalStage
) -> None:
    """CHARACTERIZATION: the document reader can refuse at every declared stage.

    The census is only a claim about the tree until each named class is shown
    to be produced by some input the reader actually meets, and the claim is
    about the whole order rather than a remembered prefix of it, so the
    population is taken from `RefusalStage` itself: a stage added to the
    declaration and reachable by nothing fails here rather than joining
    quietly.

    The refusals are read as `ValueError`s here because that is the guarantee
    every reader in this package made before the stage existed, and this test
    is what says the census did not narrow it.
    """
    assert {declared for _, declared in DOCUMENT_STAGES} == set(RefusalStage)
    with pytest.raises(ValueError) as caught:
        loads(source)
    assert getattr(caught.value, "stage", None) is stage


@pytest.mark.parametrize(("source", "stage"), DOCUMENT_STAGES)
def test_one_except_catches_every_stage_of_the_declared_order(
    source: str | bytes, stage: RefusalStage
) -> None:
    """REGRESSION: `except Refusal` catches the whole order, not a prefix of it.

    The format document publishes one numbered order and names `Refusal` as
    what carries a stage, so a caller who reads it writes `except Refusal` and
    believes it covers every rank.  While the last rank arrived through a class
    that shared no base with `Refusal` but `ValueError`, that caller caught
    eight of nine and let the ninth escape as an unhandled exception.  The
    population is `RefusalStage` itself rather than a written-out list, so a
    stage added to the order and left outside the base fails here.

    The stage and the further conditions are read as attributes rather than
    through `getattr` or an `isinstance` narrowing, because a caller having to
    guess whether the exception it caught carries them is the same defect one
    level down.
    """
    assert {declared for _, declared in DOCUMENT_STAGES} == set(RefusalStage)
    with pytest.raises(Refusal) as caught:
        loads(source)
    assert caught.value.stage is stage
    assert isinstance(caught.value.also, tuple)


def document_readers() -> frozenset[str]:
    """Return the exported name of every document reader the package publishes.

    The readers are not a list this file keeps: they are the `loads`-suffixed
    names on the package's own public surface, so a fifth one exported without
    being staged reaches these tests as a case rather than as an omission.
    """
    return frozenset(name for name in tiergraph.__all__ if name.endswith("loads"))


READER_SCOPES: dict[str, str] = {
    "loads": "",
    "grammar_loads": "",
    "selection_loads": "",
    "program_loads": "JSONL line 1: ",
}

READER_ENVELOPES: dict[str, tuple[object, str]] = {
    "loads": (wire, "document size 2 bytes exceeds limit 1"),
    "grammar_loads": (wire, "document size 2 bytes exceeds limit 1"),
    "selection_loads": (wire, "document size 2 bytes exceeds limit 1"),
    "program_loads": (machine_codec, "JSONL program exceeds 1 bytes"),
}


@pytest.mark.parametrize("reader_name", sorted(document_readers()))
@pytest.mark.parametrize(
    ("source", "stage", "message"),
    [
        (b"\xff\xfe", RefusalStage.ENCODING, "parse UTF-8 failed: invalid start byte"),
        ("not json", RefusalStage.SYNTAX, "parse JSON failed: Expecting value"),
        (
            '{"a":1,"a":2}',
            RefusalStage.SYNTAX,
            "parse JSON failed: duplicate object key 'a'",
        ),
        (
            "[" * (wire.MAX_JSON_DEPTH + 1) + "]" * (wire.MAX_JSON_DEPTH + 1),
            RefusalStage.SYNTAX,
            f"JSON nesting depth exceeds limit {wire.MAX_JSON_DEPTH}",
        ),
        (
            "\ud800",
            RefusalStage.ENCODING,
            "encode UTF-8 failed: surrogates not allowed",
        ),
        (
            '{"a":"\\ud800"}',
            RefusalStage.ENCODING,
            "a value '\\ud800' has unsupported character U+D800",
        ),
    ],
)
def test_every_reader_stages_the_text_before_reading_it(
    reader_name: str,
    source: str | bytes,
    stage: RefusalStage,
    message: str,
) -> None:
    """REGRESSION: all four document readers stage their own text alike.

    The declared order governs every document reader this package exposes, so a
    reader that let the JSON parser's own exception escape would leave a caller
    matching wording for a condition the order already names.  These are the
    conditions a reader answers before it looks at any member, and the stage
    and the wording are asserted together because a refusal at the right stage
    carrying a different account of it is a different contract.

    The population is read off the package's public surface rather than written
    out here, because a universal claim tested on a subset is how the program
    reader came to answer four of these conditions differently from the other
    three -- two of them the two spellings of one encoding condition, which it
    met by raising the encoder's own exception and by accepting the escape
    outright.  Line orientation shows up only as the scope each condition is
    reported in: the program reader says which line the text it could not read
    was on, and says nothing else differently.
    """
    assert set(READER_SCOPES) == document_readers()
    refusal = refuse(getattr(tiergraph, reader_name), source)
    assert refusal.stage is stage
    assert str(refusal) == READER_SCOPES[reader_name] + message


@pytest.mark.parametrize("reader_name", sorted(document_readers()))
def test_every_reader_measures_its_own_envelope_before_decoding(
    reader_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: the envelope is the first condition each reader meets.

    An oversized input that is also not text meets two conditions at once, and
    only a reader enforcing the envelope first reports the lower stage; a
    reader that decoded before measuring would report the encoding one.  A
    fixture carrying only one of the two could not tell those apart.

    The envelope is each reader's own rather than one shared limit, which is
    why the module the limit is read from is a parameter here.  Three readers
    measure a whole text against `wire.MAX_DOCUMENT_BYTES`; the program reader
    reads lines, so it measures a running total against its own binding of that
    name and reports the program rather than the document.  Patching `wire`
    for it would leave the limit it actually consults untouched, and the test
    would pass without exercising anything.

    The population is the package's own `loads`-suffixed public surface, so a
    reader exported without an envelope of its own arrives here as a case.
    """
    assert set(READER_ENVELOPES) == document_readers()
    limits, message = READER_ENVELOPES[reader_name]
    monkeypatch.setattr(limits, "MAX_DOCUMENT_BYTES", 1)
    refusal = refuse(getattr(tiergraph, reader_name), b"\xff\xfe")
    assert refusal.stage is RefusalStage.ENVELOPE
    assert str(refusal) == message


def test_a_graph_reference_outranks_no_further_structural_condition() -> None:
    """CHARACTERIZATION: an unresolved prefix is a reference condition.

    Spelling and resolution are separate classes: the name here is spelled the
    way the wire requires and still names nothing the document declares.
    """
    value = document()
    graph_of(value)["tiers"][0]["declaration"]["name"] = "nope:x"
    refusal = refuse(loads, json.dumps(value))
    assert refusal.stage is RefusalStage.REFERENCE
    assert str(refusal) == (
        "tiers[0].declaration.name uses undeclared namespace prefix 'nope'"
    )


def test_a_selector_settles_its_discriminator_before_its_field_set() -> None:
    """REGRESSION: the selection reader observes the same order.

    Which member names the selector is settled before anything is judged
    against it, because the field set a selector must carry depends on which
    selector it is.  An unreadable discriminator is therefore a discriminator
    condition rather than a member of the wrong construction.
    """
    both = refuse(selection_loads, '{"op":"union","select":"tier"}')
    assert both.stage is RefusalStage.DISCRIMINATOR

    unreadable = refuse(selection_loads, '{"op":1}')
    assert unreadable.stage is RefusalStage.DISCRIMINATOR
    assert str(unreadable) == "$.op must be a string"

    unknown = refuse(selection_loads, '{"select":"nope"}')
    assert unknown.stage is RefusalStage.DISCRIMINATOR


def test_a_selector_field_set_outranks_a_value_it_would_carry() -> None:
    """REGRESSION: a shape condition is primary over a value condition.

    This selector carries an unknown member and an invalid domain at once.  The
    field set is what says which members exist to be valued, so it is reported
    first; a selector with only the bad domain cannot discriminate that.
    """
    refusal = refuse(
        selection_loads,
        json.dumps(
            {
                "select": "attribute",
                "attribute": {"namespace": "urn:x", "local_name": "a"},
                "domain": "nope",
                "zz": 1,
            }
        ),
    )
    assert refusal.stage is RefusalStage.SHAPE
    assert "has unknown fields ['zz']" in str(refusal)


def test_an_execution_refusal_carries_the_last_stage() -> None:
    """REGRESSION: a promise spanning opcodes is the end of the order.

    Execution can only refuse once every earlier class has been passed, so the
    machine's own refusals sit at the last stage rather than being unstaged.
    """
    with pytest.raises(ExecutionError) as caught:
        execute([object()])
    assert caught.value.stage is RefusalStage.SEMANTICS
    assert isinstance(caught.value, Refusal)


def test_schema_generation_refusal_is_a_discriminator_condition() -> None:
    """CHARACTERIZATION: asking for a foreign schema is a version condition.

    The published schema is selected by the format version, so naming another
    one is the same class of mistake as sending a document from that format.
    """
    refusal = refuse(json_schema, "7")
    assert refusal.stage is RefusalStage.DISCRIMINATOR


def test_an_accepted_document_reports_no_condition() -> None:
    """CHARACTERIZATION: the order adds classes, never refusals.

    Every stage above is a way to refuse; a document this release implements
    must still meet none of them.
    """
    assert validation_errors(to_data(rich_graph()), FORMAT_VERSION) == []
    assert loads(json.dumps(to_data(Graph((), (), ())))) == Graph((), (), ())


def test_the_stage_vocabulary_is_reachable_where_callers_import_it() -> None:
    """REGRESSION: moving the enum down does not move where callers find it.

    The stage is declared in the base module so the graph channel can name it
    without importing upward, but every existing reader imports it from
    `tiergraph.schema`.  The re-export has to be the same object rather than a
    second enumeration, or two stages of one name would compare unequal.
    """
    assert schema.RefusalStage is core.RefusalStage
    assert schema.RefusalStage is RefusalStage
    assert schema.RefusalStage.SEMANTICS is RefusalStage.SEMANTICS
    assert "RefusalStage" in schema.__all__


def test_a_graph_refusal_carries_the_semantic_stage_by_default() -> None:
    """REGRESSION: the other refusal channel is staged too, without restating.

    A declaration or graph-contract violation is semantic by nature: the bytes
    decoded, the shapes held, and what the document says is still not sayable.
    Every existing raise site says only that much, so the default has to be the
    stage those sites mean, and the failure has to stay a `ValueError` for the
    callers that already catch one.
    """
    refusal = GraphValidationError("the graph says something unsayable")

    assert refusal.stage is RefusalStage.SEMANTICS
    assert isinstance(refusal, ValueError)
    assert str(refusal) == "the graph says something unsayable"


def test_a_graph_refusal_reports_the_stage_its_site_names() -> None:
    """CHARACTERIZATION: the default is what a site means, not all it can say.

    A site whose condition is sharper than semantics states it, which is the
    point of carrying the stage as data; a default that could not be overridden
    would rank every graph refusal alike forever.
    """
    refusal = GraphValidationError("no such tier", RefusalStage.REFERENCE)

    assert refusal.stage is RefusalStage.REFERENCE
    assert refusal.stage < RefusalStage.SEMANTICS


def test_both_refusal_channels_rank_in_one_order() -> None:
    """CHARACTERIZATION: one comparison spans both ways this package refuses.

    A caller meets a `Refusal` from the reader and a `GraphValidationError`
    from the graph, and the reason for one vocabulary is that the two can be
    compared at all.  Separate enumerations would rank each channel alone.
    """
    read = Refusal(RefusalStage.REFERENCE, "a name that does not resolve")
    built = GraphValidationError("a contract the graph breaks")

    assert read.stage < built.stage
