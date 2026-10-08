"""Exercise the scriptable graph-editing command surface."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

import tiergraph
import tiergraph.cli as cli
from tests.test_cli import _prepare_help_example
from tests.test_clock import (
    SEGMENT,
    clock_profile_data,
    fixture_with_parent,
    reference_shape,
)
from tests.test_edit_primitives import fixture as primitive_fixture
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    DurableItemRef,
    Graph,
    GraphCarrier,
    Item,
    ItemRef,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    PolyadicInstanceRef,
    QualifiedName,
    RelationInstanceRef,
    Tier,
    TierDeclaration,
    XsdType,
)


def _files(path: Path) -> Path:
    _prepare_help_example(path, [])
    return path / "graph.json"


def _run(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    status = cli.main(arguments)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_edit_controls_record_inverse_dry_run_and_in_place(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _files(tmp_path)
    original = source.read_bytes()
    report = tmp_path / "report.json"
    record = tmp_path / "record.jsonl"
    inverse = tmp_path / "inverse.jsonl"
    base = [
        "edit",
        str(source),
        "move",
        "/items/durable/alpha",
        "--to",
        "1",
    ]
    annotations = [
        "--annotate",
        "author=writer",
        "--annotate",
        'reason="correction"',
        "--annotate",
        "stage=draft",
        "--annotate",
        "confidence=0.75",
        "--annotate",
        "iteration=2",
        "--annotate",
        "tool=shell",
        "--annotate",
        "timestamp=2026-10-07",
        "--annotate",
        'batch={"name":"a"}',
    ]
    assert (
        cli.main(
            [
                *base,
                "--dry-run",
                "--report",
                str(report),
                "--record",
                str(record),
                "--inverse-out",
                str(inverse),
                "--max-steps",
                "1",
                *annotations,
            ]
        )
        == 0
    )
    assert source.read_bytes() == original
    report_data = json.loads(report.read_text(encoding="utf-8"))
    assert report_data["dry_run"] is True
    assert report_data["reports"][0]["operation"] == "move_item"
    patch = tiergraph.patch_loads(record.read_bytes())
    assert patch.annotations.to_data()["author"] == "writer"
    assert patch.annotations.to_data()["fields"] == {"batch": {"name": "a"}}
    changed = patch.apply(tiergraph.loads(original))
    assert tiergraph.patch_loads(inverse.read_bytes()).apply(
        changed
    ) == tiergraph.loads(original)

    status, output, error = _run([*base, "--dry-run"], capsys)
    assert status == 0 and error == ""
    assert json.loads(output)["dry_run"] is True

    assert cli.main([*base, "--in-place"]) == 0
    changed_bytes = source.read_bytes()
    assert changed_bytes != original
    status, _, error = _run(
        [
            "edit",
            str(source),
            "move",
            "/items/durable/alpha",
            "--to",
            "99",
            "--in-place",
        ],
        capsys,
    )
    assert status == 1 and "ValueError" in error
    assert source.read_bytes() == changed_bytes


def test_plain_edits_do_not_build_recording_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _files(tmp_path)
    output = tmp_path / "plain.json"

    def reject_journal(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("plain CLI edits must not create a journal")

    monkeypatch.setattr(tiergraph, "Journal", reject_journal)
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "move",
                "/items/durable/alpha",
                "--to",
                "1",
                "-o",
                str(output),
            ]
        )
        == 0
    )
    assert tiergraph.loads(output.read_bytes()) != tiergraph.loads(source.read_bytes())


def test_direct_edit_annotations_require_an_observable_journal_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Annotations never disappear into the allocation-free plain editor."""
    source = _files(tmp_path)
    output = tmp_path / "annotated.json"

    status, _, error = _run(
        [
            "edit",
            str(source),
            "move",
            "/items/durable/alpha",
            "--to",
            "1",
            "--annotate",
            "reason=correction",
            "-o",
            str(output),
        ],
        capsys,
    )

    assert status == 1
    assert (
        "--annotate requires --record, --inverse-out, --report, or --dry-run" in error
    )
    assert not output.exists()


def test_patch_apply_annotations_require_an_observable_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Patch-application annotations cannot be accepted and then discarded."""
    source = _files(tmp_path)
    graph = tiergraph.loads(source.read_bytes())
    changed = graph.move_item(ItemRef(QualifiedName("urn:path", "tokens"), 0), 1)
    patch_path = tmp_path / "change.jsonl"
    patch_path.write_text(
        tiergraph.patch_dumps(
            tiergraph.diff(graph, changed, tiergraph.EquivalenceView.EXACT)
        ),
        encoding="utf-8",
    )
    output = tmp_path / "annotated.json"

    status, _, error = _run(
        [
            "patch",
            "apply",
            str(patch_path),
            str(source),
            "--annotate",
            "reason=correction",
            "-o",
            str(output),
        ],
        capsys,
    )

    assert status == 1
    assert (
        "--annotate requires --record, --inverse-out, --report, or --dry-run" in error
    )
    assert not output.exists()


def test_plain_bulk_edits_do_not_build_a_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _files(tmp_path)
    output = tmp_path / "bulk.json"

    def reject_diff(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("plain bulk edits must not build a patch")

    monkeypatch.setattr(tiergraph, "diff", reject_diff)
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "bulk",
                "--selector",
                str(tmp_path / "selector.json"),
                "--set-attribute",
                str(tmp_path / "attribute.json"),
                "-o",
                str(output),
            ]
        )
        == 0
    )


def test_patch_apply_bulk_forms_and_budget_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _files(tmp_path)
    output = tmp_path / "applied.json"
    recorded = tmp_path / "replay.jsonl"
    assert (
        cli.main(
            [
                "patch",
                "apply",
                str(tmp_path / "change.jsonl"),
                str(source),
                "-o",
                str(output),
                "--record",
                str(recorded),
                "--annotate",
                "reason=replay",
            ]
        )
        == 0
    )
    assert tiergraph.loads(output.read_bytes()) != tiergraph.loads(source.read_bytes())
    assert tiergraph.patch_loads(recorded.read_bytes()).annotations.reason == "replay"
    for dry_run_command in (
        [
            "patch",
            "apply",
            str(tmp_path / "change.jsonl"),
            str(source),
        ],
        [
            "edit",
            str(source),
            "apply",
            "--patch",
            str(tmp_path / "change.jsonl"),
        ],
    ):
        status, dry_report, error = _run([*dry_run_command, "--dry-run"], capsys)
        assert status == 0 and error == ""
        assert json.loads(dry_report)["dry_run"] is True

    where_output = tmp_path / "where.json"
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "bulk",
                "--where",
                "note=marked|!note=marked",
                "--prefix",
                "p",
                "--set-attribute",
                str(tmp_path / "attribute.json"),
                "-o",
                str(where_output),
            ]
        )
        == 0
    )
    assert all(
        tiergraph.loads(where_output.read_bytes()).tiers[0].items[index].attributes
        for index in range(3)
    )
    status, dry_report, error = _run(
        [
            "edit",
            str(source),
            "bulk",
            "--where",
            "note=marked|!note=marked",
            "--prefix",
            "p",
            "--set-attribute",
            str(tmp_path / "attribute.json"),
            "--dry-run",
        ],
        capsys,
    )
    assert status == 0 and error == ""
    assert json.loads(dry_report)["selected"] == 3

    ordering = json.dumps(
        {"order": "tier", "tier": {"namespace": "urn:path", "local_name": "tokens"}}
    )
    match_output = tmp_path / "match.json"
    assert (
        cli.main(
            [
                "edit",
                str(where_output),
                "bulk",
                "--match",
                ".",
                "--ordering",
                ordering,
                "--prefix",
                "p",
                "--remove-attribute",
                "urn:path",
                "note",
                "-o",
                str(match_output),
            ]
        )
        == 0
    )
    assert all(
        not item.attributes
        for item in tiergraph.loads(match_output.read_bytes()).tiers[0].items
    )
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "bulk",
                "--match",
                ".",
                "--ordering",
                ordering,
                "--set-attribute",
                str(tmp_path / "attribute.json"),
                "-o",
                str(tmp_path / "match-no-prefix.json"),
            ]
        )
        == 0
    )

    one = tmp_path / "one.json"
    one.write_text(
        json.dumps({"select": "item", "path": "/items/durable/gamma"}),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "bulk",
                "--selector",
                str(one),
                "--delete",
                "-o",
                str(tmp_path / "deleted.json"),
            ]
        )
        == 0
    )
    status, _, error = _run(
        [
            "edit",
            str(source),
            "bulk",
            "--selector",
            str(tmp_path / "selector.json"),
            "--delete",
            "--max-steps",
            "1",
            "-o",
            str(tmp_path / "refused.json"),
        ],
        capsys,
    )
    assert status == 1 and "BudgetExhausted" in error
    assert not (tmp_path / "refused.json").exists()


@pytest.mark.parametrize("surface", ["edit", "patch"])
def test_patch_application_budget_counts_operations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    surface: str,
) -> None:
    source = _files(tmp_path)
    graph = tiergraph.loads(source.read_bytes())
    changed = graph.move_item(ItemRef(QualifiedName("urn:path", "tokens"), 0), 1)
    forward = tiergraph.diff(graph, changed, tiergraph.EquivalenceView.EXACT)
    backward = tiergraph.diff(changed, graph, tiergraph.EquivalenceView.EXACT)
    patch_path = tmp_path / "two-operations.jsonl"
    patch_path.write_text(
        tiergraph.patch_dumps(tiergraph.compose_patches(forward, backward)),
        encoding="utf-8",
    )
    output = tmp_path / f"{surface}-result.json"
    command = (
        ["edit", str(source), "apply", "--patch", str(patch_path)]
        if surface == "edit"
        else ["patch", "apply", str(patch_path), str(source)]
    )

    status, _, error = _run([*command, "--max-steps", "1", "-o", str(output)], capsys)

    assert status == 1
    assert "BudgetExhausted" in error
    assert not output.exists()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("relation:2", RelationInstanceRef(2)),
        ("polyadic:3", PolyadicInstanceRef(3)),
        ("relation-id:r", tiergraph.DurableRelationRef("r")),
        ("polyadic-id:p", tiergraph.DurablePolyadicRef("p")),
    ],
)
def test_relation_target_spellings(text: str, expected: object) -> None:
    assert cli._relation_target(text) == expected


@pytest.mark.parametrize("text", ["missing", "unknown:0"])
def test_relation_target_refusals(text: str) -> None:
    with pytest.raises(ValueError):
        cli._relation_target(text)


def test_annotation_and_target_validation(tmp_path: Path) -> None:
    source = _files(tmp_path)
    graph = tiergraph.loads(source.read_bytes())
    annotations = cli._edit_annotations(
        ["author=plain", "confidence=1", "iteration=3", "extra=[1,true]"]
    )
    assert annotations.author == "plain"
    assert annotations.fields == {"extra": [1, True]}
    for value in ("bad", "author=1", "confidence=true", "iteration=1.5"):
        with pytest.raises(ValueError):
            cli._edit_annotations([value])
    assert cli._edit_target(graph, "document") is None
    assert cli._edit_target(graph, "tier:urn:path|tokens") == QualifiedName(
        "urn:path", "tokens"
    )
    assert cli._edit_target(
        graph, "relation-declaration:urn:path|link"
    ) == QualifiedName("urn:path", "link")
    assert cli._edit_target(graph, "relation:0") == RelationInstanceRef(0)
    with pytest.raises(ValueError):
        cli._qualified_spelling("urn:path")
    assert cli._seal_carrier("relations") is GraphCarrier.RELATIONS
    assert cli._seal_carrier("polyadic-relations") is GraphCarrier.POLYADIC_RELATIONS


def test_json_operand_decoders_and_refusals(tmp_path: Path) -> None:
    case = primitive_fixture("text")
    values: list[Any] = [
        case.graph.namespaces[0],
        case.graph.tiers[0].declaration,
        case.graph.attribute_declarations[0],
        case.graph.relation_declarations[0],
    ]
    for index, value in enumerate(values):
        path = tmp_path / f"declaration-{index}.json"
        path.write_text(json.dumps(value.to_data()), encoding="utf-8")
        assert cli._declaration_json(str(path)) == value
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(tiergraph.Refusal):
        cli._declaration_json(str(bad))
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(tiergraph.Refusal):
        cli._endpoint_array(str(bad))


def test_layer_subjects_and_actions(tmp_path: Path) -> None:
    source = _files(tmp_path)
    graph = tiergraph.loads(source.read_bytes())
    assert cli._layer_subject(graph, "document") == tiergraph.DocumentRef()
    assert cli._layer_subject(graph, "tier:urn:path|tokens") == tiergraph.TierRef(
        QualifiedName("urn:path", "tokens")
    )
    assert cli._layer_subject(
        graph, "relation-declaration:urn:path|link"
    ) == tiergraph.RelationDeclarationRef(QualifiedName("urn:path", "link"))
    assert cli._layer_subject(graph, "relation:0") == RelationInstanceRef(0)
    assert cli._layer_subject(
        graph, "/items/durable/alpha"
    ) == tiergraph.DurableItemRef("alpha")

    layer = LayerName("urn:path", "hand")
    valued = graph.add_layer(layer).put_fact(
        layer,
        LayerFact(
            tiergraph.DurableItemRef("alpha"),
            AttributeValue(QualifiedName("urn:path", "note"), XsdType.STRING, "x"),
        ),
    )
    source.write_bytes(tiergraph.dump_bytes(valued))
    fact = tmp_path / "fact.json"
    layer_data = cast(dict[str, Any], valued.layers[0].to_data())
    encoded_fact = cast(list[object], layer_data["facts"])[0]
    fact.write_text(json.dumps(encoded_fact), encoding="utf-8")
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "layer",
                "remove-fact",
                "--vocabulary",
                "urn:path",
                "--source",
                "hand",
                "--subject",
                "/items/durable/alpha",
                "--name",
                "urn:path",
                "note",
                "-o",
                str(tmp_path / "removed-fact.json"),
            ]
        )
        == 0
    )
    for action, extras in (
        ("add", ["--fact", str(fact)]),
        ("remove", ["--subject", "document"]),
        ("put-fact", []),
        ("remove-fact", []),
    ):
        args = argparse.Namespace(
            action=action,
            vocabulary="urn:new",
            source="source",
            fact=None,
            subject=None,
            name=None,
        )
        if extras:
            args.fact = str(fact) if extras[0] == "--fact" else None
            args.subject = "document" if extras[0] == "--subject" else None
        with pytest.raises(ValueError):
            cli._edit_layer(graph.edit(), graph, args)


def test_full_replacement_policy_codec_and_refusals(tmp_path: Path) -> None:
    qname = {"namespace": "urn:path", "local_name": "tokens"}
    item = {"tier": qname, "index": 0}
    policy = {
        "default": "follow",
        "correspond": True,
        "correspondence": [{"old": item, "new": [item]}],
        "relations": [
            {
                "name": {"namespace": "urn:path", "local_name": "contains"},
                "action": "follow",
            }
        ],
        "layers": [
            {"name": {"vocabulary": "urn:path", "source": "hand"}, "action": "split"}
        ],
        "insertion_points": [{"name": qname, "index": 1}],
    }
    path = tmp_path / "policies.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    decoded = cli._replacement_policies(str(path))
    assert decoded.correspond and decoded.default is tiergraph.ReplacementAction.FOLLOW
    assert decoded.insertion_points == {QualifiedName("urn:path", "tokens"): 1}
    assert cli._replacement_policies(None) == tiergraph.ReplacementPolicies()

    invalid: list[object] = [
        [],
        {"unknown": 1},
        {"correspondence": {}},
        {"correspondence": [{}]},
        {"correspondence": [{"old": item, "new": {}}]},
        {"relations": {}},
        {"relations": [{}]},
        {"relations": [{"name": qname, "action": 1}]},
        {"layers": [{"name": {"vocabulary": "v", "source": "s"}, "action": 1}]},
        {"insertion_points": [{"name": qname, "index": True}]},
        {"correspond": "yes"},
        {"default": 1},
    ]
    for index, value in enumerate(invalid):
        bad = tmp_path / f"bad-policy-{index}.json"
        bad.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises((tiergraph.Refusal, TypeError, ValueError)):
            cli._replacement_policies(str(bad))


def test_identity_helpers_cover_all_kinds_and_refuse_mismatches(tmp_path: Path) -> None:
    tier = QualifiedName("urn:id", "items")
    plain = Graph(
        (NamespaceDeclaration("i", "urn:id"),),
        (Tier(TierDeclaration(tier, "Items"), (Item(), Item())),),
        (),
    )
    editor = plain.edit()
    cli._promote(editor, plain, "item", "/items/structural/urn:id/items/0", "item")
    cli._promote(
        editor, plain, "boundary", "/positions/structural/urn:id/items/1", "edge"
    )
    promoted = editor.freeze()
    editor = promoted.edit()
    cli._demote(editor, promoted, "boundary", "/positions/durable/item/edge/before")
    cli._demote(editor, promoted, "item", "/items/durable/item")

    case = primitive_fixture("text")
    relation_graph, _ = case.graph.promote_relation(RelationInstanceRef(0), "binary")
    relation_graph, _ = relation_graph.promote_relation(PolyadicInstanceRef(0), "many")
    editor = relation_graph.edit()
    cli._demote(editor, relation_graph, "relation", "relation-id:binary")
    cli._demote(editor, relation_graph, "polyadic", "polyadic-id:many")
    editor = case.graph.edit()
    cli._promote(editor, case.graph, "relation", "relation:0", "binary")
    cli._promote(editor, case.graph, "polyadic", "polyadic:0", "many")

    for kind, target in (
        ("relation", "polyadic:0"),
        ("polyadic", "relation:0"),
    ):
        with pytest.raises(ValueError):
            cli._promote(case.graph.edit(), case.graph, kind, target, "bad")
    for kind, target in (
        ("item", "/items/structural/urn:id/items/0"),
        ("boundary", "/positions/structural/urn:id/items/0"),
        ("relation", "relation:0"),
        ("polyadic", "polyadic:0"),
    ):
        with pytest.raises(ValueError):
            cli._demote(plain.edit(), plain, kind, target)


def test_destination_and_stdin_in_place_refusals(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _files(tmp_path)
    with pytest.raises(ValueError):
        cli._distinct_destinations(["-", "-"])
    cli._distinct_destinations([None, str(tmp_path / "a"), str(tmp_path / "b")])

    stdin = io.TextIOWrapper(io.BytesIO(source.read_bytes()), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    status, _, error = _run(
        [
            "edit",
            "-",
            "move",
            "/items/durable/alpha",
            "--to",
            "1",
            "--in-place",
        ],
        capsys,
    )
    assert status == 1 and "--in-place requires a graph file" in error

    status, _, error = _run(
        [
            "edit",
            str(source),
            "move",
            "/items/durable/alpha",
            "--to",
            "1",
            "--record",
            str(source),
        ],
        capsys,
    )
    assert status == 1 and "input and output paths must differ" in error

    item_operand = tmp_path / "item.json"
    item_before = item_operand.read_bytes()
    status, _, error = _run(
        [
            "edit",
            str(source),
            "insert",
            "--tier",
            "urn:path",
            "tokens",
            "--at",
            "1",
            "--item",
            str(item_operand),
            "-o",
            str(item_operand),
        ],
        capsys,
    )
    assert status == 1 and "input and output paths must differ" in error
    assert item_operand.read_bytes() == item_before

    patch = tmp_path / "change.jsonl"
    status, _, error = _run(
        [
            "patch",
            "apply",
            str(patch),
            str(source),
            "--record",
            str(patch),
        ],
        capsys,
    )
    assert status == 1 and "input and output paths must differ" in error


def test_remaining_edit_variants_and_argument_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _files(tmp_path)
    graph = tiergraph.loads(source.read_bytes())

    plain = Graph(
        graph.namespaces,
        (Tier(graph.tiers[0].declaration, graph.tiers[0].items),),
        (),
    )
    plain_path = tmp_path / "plain.json"
    plain_path.write_bytes(tiergraph.dump_bytes(plain))
    status, _, _ = _run(
        [
            "edit",
            str(plain_path),
            "delete",
            "/items/durable/beta",
            "--count",
            "2",
            "-o",
            str(tmp_path / "run-removed.json"),
        ],
        capsys,
    )
    assert status == 0
    status, _, error = _run(
        [
            "edit",
            str(plain_path),
            "delete",
            "/items/durable/alpha",
            "--count",
            "0",
            "-o",
            str(tmp_path / "bad-count.json"),
        ],
        capsys,
    )
    assert status == 1 and "--count must be positive" in error

    valued = graph.set_attribute(
        ItemRef(QualifiedName("urn:path", "tokens"), 0),
        AttributeValue(QualifiedName("urn:path", "note"), XsdType.STRING, "x"),
    )
    valued_path = tmp_path / "valued.json"
    valued_path.write_bytes(tiergraph.dump_bytes(valued))
    assert (
        cli.main(
            [
                "edit",
                str(valued_path),
                "feature",
                "remove",
                "/items/durable/alpha",
                "--name",
                "urn:path",
                "note",
                "-o",
                str(tmp_path / "unvalued.json"),
            ]
        )
        == 0
    )
    for action, options in (
        ("set", []),
        ("remove", ["--attribute", str(tmp_path / "attribute.json")]),
    ):
        status, _, error = _run(
            [
                "edit",
                str(source),
                "feature",
                action,
                "/items/durable/alpha",
                *options,
                "-o",
                str(tmp_path / f"bad-feature-{action}.json"),
            ],
            capsys,
        )
        assert status == 1 and f"feature {action} requires" in error

    assert (
        cli.main(
            [
                "edit",
                str(source),
                "undeclare",
                "--prefix",
                "extra",
                "--cascade",
                "-o",
                str(tmp_path / "cascade.json"),
            ]
        )
        == 0
    )

    sealed = graph.seal(QualifiedName("urn:path", "tokens"), 2)
    sealed_path = tmp_path / "sealed.json"
    sealed_path.write_bytes(tiergraph.dump_bytes(sealed))
    assert (
        cli.main(
            [
                "edit",
                str(sealed_path),
                "seal",
                "unseal",
                "urn:path|tokens",
                "--sealed",
                "1",
                "-o",
                str(tmp_path / "shortened.json"),
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "edit",
                str(sealed_path),
                "seal",
                "drop",
                "urn:path|tokens",
                "-o",
                str(tmp_path / "dropped.json"),
            ]
        )
        == 0
    )
    for action, options in (("drop", ["--sealed", "1"]), ("set", [])):
        status, _, error = _run(
            [
                "edit",
                str(source),
                "seal",
                action,
                "urn:path|tokens",
                *options,
                "-o",
                str(tmp_path / f"bad-seal-{action}.json"),
            ],
            capsys,
        )
        assert status == 1 and "seal" in error


def test_polyadic_endpoints_and_binary_arity_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = primitive_fixture("text")
    source = tmp_path / "graph.json"
    source.write_bytes(tiergraph.dump_bytes(case.graph))
    endpoints = tmp_path / "endpoints.json"
    endpoints.write_text(
        json.dumps([ItemRef(case.unit, 1).to_data(), ItemRef(case.unit, 2).to_data()]),
        encoding="utf-8",
    )
    one = tmp_path / "one.json"
    one.write_text(json.dumps([ItemRef(case.unit, 0).to_data()]), encoding="utf-8")
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "endpoints",
                "polyadic:0",
                "--sources",
                str(endpoints),
                "--targets",
                str(one),
                "-o",
                str(tmp_path / "changed.json"),
            ]
        )
        == 0
    )
    status, _, error = _run(
        [
            "edit",
            str(source),
            "endpoints",
            "relation:0",
            "--sources",
            str(endpoints),
            "--targets",
            str(one),
            "-o",
            str(tmp_path / "bad.json"),
        ],
        capsys,
    )
    assert status == 1 and "endpoint arrays need one entry" in error


def test_bulk_relation_delete_unsupported_node_and_option_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _files(tmp_path)
    graph = tiergraph.loads(source.read_bytes())
    relation_note = QualifiedName("urn:path", "relation-note")
    graph = graph.declare(
        AttributeDeclaration(
            relation_note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
        )
    ).set_attribute(
        RelationInstanceRef(0), AttributeValue(relation_note, XsdType.STRING, "x")
    )
    source.write_bytes(tiergraph.dump_bytes(graph))
    relation_selector = tmp_path / "relations.json"
    relation_selector.write_text(
        json.dumps(
            {
                "select": "attribute",
                "attribute": relation_note.to_data(),
                "domain": "relation_instance",
            }
        ),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "bulk",
                "--selector",
                str(relation_selector),
                "--delete",
                "-o",
                str(tmp_path / "without-relation.json"),
            ]
        )
        == 0
    )

    tier_selector = tmp_path / "tier.json"
    tier_selector.write_text(
        json.dumps(
            {"select": "tier", "tier": QualifiedName("urn:path", "tokens").to_data()}
        ),
        encoding="utf-8",
    )
    status, _, error = _run(
        [
            "edit",
            str(source),
            "bulk",
            "--selector",
            str(tier_selector),
            "--delete",
            "-o",
            str(tmp_path / "bad-tier-delete.json"),
        ],
        capsys,
    )
    assert status == 1 and "bulk delete does not accept tier" in error

    invalid = (
        ["--selector", str(tier_selector), "--prefix", "p"],
        ["--where", "x", "--ordering", "{}"],
        ["--match", "."],
    )
    for index, selection in enumerate(invalid):
        status, _, _ = _run(
            [
                "edit",
                str(source),
                "bulk",
                *selection,
                "--delete",
                "-o",
                str(tmp_path / f"bad-selection-{index}.json"),
            ],
            capsys,
        )
        assert status == 1


def test_node_target_and_layer_happy_paths(tmp_path: Path) -> None:
    qname = QualifiedName("urn:test", "tier")
    assert cli._node_target(tiergraph.Node(tiergraph.NodeKind.DOCUMENT, None)) is None
    assert cli._node_target(tiergraph.Node(tiergraph.NodeKind.TIER, qname)) == qname
    assert cli._node_target(
        tiergraph.Node(tiergraph.NodeKind.RELATION_INSTANCE, 2)
    ) == RelationInstanceRef(2)
    assert cli._node_target(
        tiergraph.Node(tiergraph.NodeKind.POLYADIC_RELATION_INSTANCE, 3)
    ) == PolyadicInstanceRef(3)

    source = _files(tmp_path)
    graph = tiergraph.loads(source.read_bytes())
    layer = LayerName("urn:path", "hand")
    editor = graph.edit()
    cli._edit_layer(
        editor,
        graph,
        argparse.Namespace(
            action="add",
            vocabulary=layer.vocabulary,
            source=layer.source,
            fact=None,
            subject=None,
            name=None,
        ),
    )
    graph = editor.freeze()
    fact = LayerFact(
        tiergraph.DurableItemRef("alpha"),
        AttributeValue(QualifiedName("urn:path", "note"), XsdType.STRING, "x"),
    )
    fact_path = tmp_path / "fact.json"
    fact_data = tiergraph.Layer(layer, (fact,)).to_data()
    fact_path.write_text(
        json.dumps(cast(list[object], fact_data["facts"])[0]), encoding="utf-8"
    )
    editor = graph.edit()
    cli._edit_layer(
        editor,
        graph,
        argparse.Namespace(
            action="put-fact",
            vocabulary=layer.vocabulary,
            source=layer.source,
            fact=str(fact_path),
            subject=None,
            name=None,
        ),
    )
    graph = editor.freeze()
    assert graph.layers[0].facts == (fact,)
    empty_layer_graph = tiergraph.loads(source.read_bytes()).add_layer(layer)
    editor = empty_layer_graph.edit()
    cli._edit_layer(
        editor,
        empty_layer_graph,
        argparse.Namespace(
            action="remove",
            vocabulary=layer.vocabulary,
            source=layer.source,
            fact=None,
            subject=None,
            name=None,
        ),
    )


def test_atomic_replace_cleanup_and_diff_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "graph.json"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(tiergraph.Refusal):
        cli._atomic_graph_replace(str(target), b"not a graph")
    assert target.read_text(encoding="utf-8") == "original"
    assert not tuple(tmp_path.glob(".graph.json.*"))

    target.write_bytes(tiergraph.dump_bytes(Graph((), (), ())))
    target.chmod(0o640)
    cli._atomic_graph_replace(str(target), tiergraph.dump_bytes(Graph((), (), ())))
    assert target.stat().st_mode & 0o7777 == 0o640

    source = Graph((), (), ())
    source_path = tmp_path / "source.json"
    target_path = tmp_path / "target.json"
    source_path.write_bytes(tiergraph.dump_bytes(source))
    target_path.write_bytes(tiergraph.dump_bytes(source))
    args = argparse.Namespace(
        file=str(source_path),
        target=str(target_path),
        view="functional",
        check=True,
        output=str(tmp_path / "patch.jsonl"),
    )
    monkeypatch.setattr(tiergraph, "equivalent", lambda *unused: False)
    with pytest.raises(ValueError, match="diff replay"):
        cli._handle_diff(args)


def test_clock_profile_requires_and_uses_a_named_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = reference_shape()
    source = tmp_path / "clock.json"
    profile = tmp_path / "profile.json"
    source.write_bytes(tiergraph.dump_bytes(graph))
    profile.write_text(json.dumps(clock_profile_data()), encoding="utf-8")
    target = f"/items/structural/{SEGMENT.namespace}/{SEGMENT.local_name}/0"
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "move",
                target,
                "--to",
                "1",
                "--clock-profile",
                str(profile),
                "--rebinding",
                "keep-earlier",
                "-o",
                str(tmp_path / "moved.json"),
            ]
        )
        == 0
    )
    for options, message in (
        (["--rebinding", "keep-earlier"], "requires --clock-profile"),
        (["--clock-profile", str(profile)], "requires --rebinding"),
    ):
        status, _, error = _run(
            [
                "edit",
                str(source),
                "move",
                target,
                "--to",
                "1",
                *options,
                "-o",
                str(tmp_path / "refused.json"),
            ],
            capsys,
        )
        assert status == 1 and message in error

    ordinary_dir = tmp_path / "ordinary"
    ordinary_dir.mkdir()
    ordinary = _files(ordinary_dir)
    for command in (
        [
            "edit",
            str(ordinary),
            "bulk",
            "--selector",
            str(ordinary.parent / "selector.json"),
            "--delete",
        ],
        [
            "patch",
            "apply",
            str(ordinary.parent / "change.jsonl"),
            str(ordinary),
        ],
        [
            "edit",
            str(ordinary),
            "apply",
            "--patch",
            str(ordinary.parent / "change.jsonl"),
        ],
    ):
        with pytest.raises(SystemExit) as stopped:
            cli.main(
                [
                    *command,
                    "--clock-profile",
                    str(profile),
                    "--rebinding",
                    "keep-earlier",
                ]
            )
        assert stopped.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err


def test_clock_profile_reparents_and_refuses_unsupported_edits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = fixture_with_parent()
    source = tmp_path / "clock.json"
    profile = tmp_path / "profile.json"
    sources = tmp_path / "sources.json"
    targets = tmp_path / "targets.json"
    source.write_bytes(tiergraph.dump_bytes(graph))
    profile_data = clock_profile_data()
    for field in (
        "tick_attribute",
        "gap_attribute",
        "untimed_attribute",
        "start_attribute",
        "duration_attribute",
    ):
        profile_data[field] = None
    profile.write_text(json.dumps(profile_data), encoding="utf-8")
    sources.write_text(
        json.dumps([DurableItemRef("segment-1").to_data()]), encoding="utf-8"
    )
    targets.write_text(
        json.dumps([DurableItemRef("segment-0").to_data()]), encoding="utf-8"
    )
    output = tmp_path / "reparented.json"
    assert (
        cli.main(
            [
                "edit",
                str(source),
                "endpoints",
                "relation:3",
                "--sources",
                str(sources),
                "--targets",
                str(targets),
                "--clock-profile",
                str(profile),
                "--rebinding",
                "keep-earlier",
                "-o",
                str(output),
            ]
        )
        == 0
    )
    changed = tiergraph.loads(output.read_bytes())
    tiergraph.ClockProfile.from_data(changed, profile_data)
    assert changed.relations[3].left == DurableItemRef("segment-1")

    status, dry_report, error = _run(
        [
            "edit",
            str(source),
            "undeclare",
            "--prefix",
            "clock",
            "--cascade",
            "--clock-profile",
            str(profile),
            "--rebinding",
            "keep-earlier",
            "--dry-run",
        ],
        capsys,
    )
    assert status == 0 and error == ""
    assert json.loads(dry_report)["reports"][0]["operation"] == (
        "undeclare_with_contents"
    )

    status, _, error = _run(
        [
            "edit",
            str(source),
            "undeclare",
            "--prefix",
            "clock",
            "--clock-profile",
            str(profile),
            "--rebinding",
            "keep-earlier",
            "--dry-run",
        ],
        capsys,
    )
    assert status == 1
    assert "edit undeclare cannot run through a clock profile" in error

    with pytest.raises(SystemExit) as stopped:
        cli.main(
            [
                "edit",
                str(source),
                "replace",
                "/items/durable/segment-0",
                "--item",
                str(tmp_path / "missing.json"),
                "--clock-profile",
                str(profile),
                "--rebinding",
                "keep-earlier",
            ]
        )
    assert stopped.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_exact_rollback_verifier_rejects_a_different_graph() -> None:
    base = Graph((), (), ())
    restored = Graph((NamespaceDeclaration("p", "urn:other"),), (), ())
    with pytest.raises(tiergraph.GraphValidationError, match="did not restore"):
        cli._verify_exact_rollback(base, restored)
