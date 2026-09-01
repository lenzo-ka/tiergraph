"""One declared total order governs every document reader's refusals."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from tests.test_wire import rich_graph
from tiergraph import (
    FORMAT_VERSION,
    ExecutionError,
    Graph,
    GraphValidationError,
    core,
    execute,
    grammar_loads,
    loads,
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


@pytest.mark.parametrize(
    ("source", "stage"),
    [
        (b"\xff\xfe", RefusalStage.ENCODING),
        ("{", RefusalStage.SYNTAX),
        ("[]", RefusalStage.CONSTRUCTION),
        ('{"graph":{}}', RefusalStage.DISCRIMINATOR),
        ('{"format_version":"0.2.0","graph":{},"zz":1}', RefusalStage.SHAPE),
    ],
)
def test_each_document_reading_stage_is_reachable(
    source: str | bytes, stage: RefusalStage
) -> None:
    """CHARACTERIZATION: the document reader can refuse at each early stage.

    The census is only a claim about the tree until each named class is shown
    to be produced by some input the reader actually meets.
    """
    assert refuse(loads, source).stage is stage


def test_a_document_over_the_size_limit_outranks_its_own_bad_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: two conditions hold at once and the lower stage is reported.

    These bytes are both over the accepted size and not text.  The envelope
    condition explains the encoding one - a reader that will not hold the input
    never decodes it - so the size is primary, and a fixture carrying only one
    of the two could not tell that apart.
    """
    monkeypatch.setattr(wire, "MAX_DOCUMENT_BYTES", 1)
    refusal = refuse(loads, b"\xff\xfe")
    assert refusal.stage is RefusalStage.ENVELOPE
    assert str(refusal) == "document size 2 bytes exceeds limit 1"


@pytest.mark.parametrize("reader", [grammar_loads, selection_loads])
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
    ],
)
def test_every_reader_stages_the_text_before_reading_it(
    reader: object, source: str | bytes, stage: RefusalStage, message: str
) -> None:
    """REGRESSION: the grammar and selector readers stage their own text.

    The declared order governs every document reader this package exposes, so
    a reader that
    let the JSON parser's own exception escape would leave a caller matching
    wording for a condition the order already names.  These are the conditions
    the document reader answers before it looks at any member, and the stage
    and the wording are asserted together because a refusal at the right stage
    carrying a different account of it is a different contract.
    """
    refusal = refuse(reader, source)
    assert refusal.stage is stage
    assert str(refusal) == message
    assert refuse(loads, source).stage is stage
    assert str(refuse(loads, source)) == message


@pytest.mark.parametrize("reader", [grammar_loads, selection_loads])
def test_every_reader_holds_the_same_envelope_before_decoding(
    reader: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: the shared envelope limit is the first condition each meets.

    An oversized input that is also not text meets two conditions at once, and
    only a reader enforcing the envelope first reports the lower stage; a
    reader that decoded before measuring would report the encoding one.
    """
    monkeypatch.setattr(wire, "MAX_DOCUMENT_BYTES", 1)
    refusal = refuse(reader, b"\xff\xfe")
    assert refusal.stage is RefusalStage.ENVELOPE
    assert str(refusal) == "document size 2 bytes exceeds limit 1"


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
