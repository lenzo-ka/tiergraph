"""Bulk edits materialize selection and follow cumulative displacement."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeSelector,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BudgetExhausted,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    ItemsSelector,
    NamespaceDeclaration,
    Node,
    NodeKind,
    NodeSet,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    Refusal,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    StructuralPathProfile,
    Tier,
    TierDeclaration,
    WorkBudget,
    WorkMeter,
    XsdType,
    apply_selected,
)
from tiergraph.match import Extent, SpanMatch, SpanMatches

NAMESPACE = "urn:test:apply-selected"
TIER = QualifiedName(NAMESPACE, "unit")
MEMBERS = QualifiedName(NAMESPACE, "members")
ITEM_TYPE = QualifiedName(NAMESPACE, "Unit")
LINK = QualifiedName(NAMESPACE, "link")
GROUP = QualifiedName(NAMESPACE, "group")
MARK = QualifiedName(NAMESPACE, "mark")


def plain_graph() -> Graph:
    """Return three unreferenced ordered items for structural bulk edits."""
    return Graph(
        (NamespaceDeclaration("s", NAMESPACE),),
        (
            Tier(
                TierDeclaration(TIER, "Unit"),
                (Item("zero"), Item("one"), Item("two")),
            ),
        ),
        (SimpleRelationDeclaration(MEMBERS, TIER, ITEM_TYPE),),
    )


def rich_graph() -> Graph:
    """Return every selectable positional kind in one small graph."""
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (TIER,))
    return Graph(
        (NamespaceDeclaration("s", NAMESPACE),),
        (
            Tier(
                TierDeclaration(TIER, "Unit"),
                (Item("zero"), Item("one"), Item("two")),
            ),
        ),
        (
            SimpleRelationDeclaration(MEMBERS, TIER, ITEM_TYPE),
            BipartiteRelationDeclaration(LINK, ITEM_TYPE, ITEM_TYPE),
            PolyadicRelationDeclaration(GROUP, side, side),
        ),
        (
            RelationInstance(LINK, ItemRef(TIER, 0), ItemRef(TIER, 1)),
            RelationInstance(LINK, ItemRef(TIER, 1), ItemRef(TIER, 2)),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                GROUP,
                (ItemRef(TIER, 0),),
                (ItemRef(TIER, 2),),
            ),
        ),
    )


def item_reference(node: Node) -> ItemRef:
    """Narrow one callback target to its item coordinate."""
    assert node.kind is NodeKind.ITEM
    return cast(ItemRef, node.reference)


def test_selector_materializes_once_and_removes_every_selected_item() -> None:
    """A shrinking graph does not cause later selector results to disappear."""
    source = plain_graph()
    seen: list[ItemRef] = []
    editors: set[int] = set()

    def remove(editor: GraphEditor, node: Node) -> None:
        editors.add(id(editor))
        reference = item_reference(node)
        seen.append(reference)
        editor.remove_item(reference)

    result = apply_selected(source, ItemsSelector(TIER), remove)
    assert seen == [ItemRef(TIER, 2), ItemRef(TIER, 1), ItemRef(TIER, 0)]
    assert len(editors) == 1
    assert result.tiers[0].items == ()
    assert source.tiers[0].items == (Item("zero"), Item("one"), Item("two"))


def test_every_node_kind_uses_reverse_dependency_order() -> None:
    """Mixed selections remove instances before visiting structural endpoints."""
    source = rich_graph()
    selected = NodeSet(
        source,
        (
            Node(NodeKind.DOCUMENT, None),
            Node(NodeKind.TIER, TIER),
            Node(NodeKind.ITEM, ItemRef(TIER, 0)),
            Node(NodeKind.BOUNDARY, BoundaryRef(TIER, 0)),
            Node(NodeKind.RELATION_DECLARATION, LINK),
            Node(NodeKind.RELATION_INSTANCE, 0),
            Node(NodeKind.POLYADIC_RELATION_INSTANCE, 0),
        ),
    )
    seen: list[Node] = []

    def edit(editor: GraphEditor, node: Node) -> None:
        seen.append(node)
        if node.kind is NodeKind.RELATION_INSTANCE:
            editor.remove_relation(cast(int, node.reference))
        elif node.kind is NodeKind.POLYADIC_RELATION_INSTANCE:
            editor.remove_relation(PolyadicInstanceRef(cast(int, node.reference)))

    result = apply_selected(source, selected, edit)
    assert [node.kind for node in seen] == [
        NodeKind.POLYADIC_RELATION_INSTANCE,
        NodeKind.RELATION_INSTANCE,
        NodeKind.RELATION_DECLARATION,
        NodeKind.BOUNDARY,
        NodeKind.ITEM,
        NodeKind.TIER,
        NodeKind.DOCUMENT,
    ]
    assert len(result.relations) == 1
    assert not result.polyadic_relations


def test_relation_positions_follow_preceding_removals() -> None:
    """Each original relation index resolves again after the prior removal."""
    source = rich_graph()
    selected = NodeSet(
        source,
        (
            Node(NodeKind.RELATION_INSTANCE, 0),
            Node(NodeKind.RELATION_INSTANCE, 1),
        ),
    )
    seen: list[int] = []

    def remove(editor: GraphEditor, node: Node) -> None:
        index = cast(int, node.reference)
        seen.append(index)
        editor.remove_relation(index)

    result = apply_selected(source, selected, remove)
    assert seen == [1, 0]
    assert not result.relations


def test_overlapping_exhaustive_spans_edit_each_item_once() -> None:
    """Span materialization deduplicates overlap before any callback runs."""
    source = plain_graph()
    nodes = tuple(Node(NodeKind.ITEM, ItemRef(TIER, index)) for index in range(3))
    spans = SpanMatches(
        (
            SpanMatch(0, 0, 2, nodes[:2], None),
            SpanMatch(0, 1, 3, nodes[1:], None),
        ),
        Extent.EXHAUSTIVE,
    )
    seen: list[ItemRef] = []

    def replace(editor: GraphEditor, node: Node) -> None:
        reference = item_reference(node)
        seen.append(reference)
        editor.replace_item(reference, editor.freeze().tiers[0].items[reference.index])

    meter = WorkMeter(WorkBudget(steps=100))
    result = apply_selected(source, spans, replace, budget=meter)
    assert seen == [ItemRef(TIER, 2), ItemRef(TIER, 1), ItemRef(TIER, 0)]
    assert result == source
    assert meter.spent > len(seen)
    assert (
        apply_selected(
            source,
            SpanMatches((), Extent.EXHAUSTIVE),
            lambda _editor, _node: None,
        )
        == source
    )


@pytest.mark.parametrize("extent", (Extent.CUT_AT_BOUND, Extent.CUT_AT_BUDGET))
def test_truncated_spans_refuse_before_editing(extent: Extent) -> None:
    """A partial match witness never becomes a silent partial bulk edit."""
    source = plain_graph()
    spans = SpanMatches(
        (
            SpanMatch(
                0,
                0,
                1,
                (Node(NodeKind.ITEM, ItemRef(TIER, 0)),),
                None,
            ),
        ),
        extent,
    )
    calls = 0

    def count(_editor: GraphEditor, _node: Node) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(Refusal, match="must be exhaustive"):
        apply_selected(source, spans, count)
    assert calls == 0


def test_node_set_must_belong_to_the_edited_graph() -> None:
    """Equal content in a different graph object does not authorize editing."""
    source = plain_graph()
    other = plain_graph()
    selected = NodeSet(other, (Node(NodeKind.ITEM, ItemRef(TIER, 0)),))
    with pytest.raises(Refusal, match="must belong"):
        apply_selected(source, selected, lambda editor, node: None)


def test_selector_accepts_an_explicit_path_profile() -> None:
    """Bulk selection forwards a caller's path interpretation exactly once."""
    source = plain_graph()
    seen: list[Node] = []
    result = apply_selected(
        source,
        ItemsSelector(TIER),
        lambda _editor, node: seen.append(node),
        path_profile=StructuralPathProfile(),
    )
    assert result == source
    assert len(seen) == 3


def test_named_selector_does_not_validate_between_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named-node remapping reads the editor without freezing its graph."""
    value = AttributeValue(MARK, XsdType.STRING, "selected")
    source = Graph(
        (NamespaceDeclaration("s", NAMESPACE),),
        tuple(
            Tier(
                TierDeclaration(QualifiedName(NAMESPACE, f"tier-{index}"), "Tier"),
                (),
                (value,),
            )
            for index in range(3)
        ),
        (),
        attribute_declarations=(
            AttributeDeclaration(MARK, AttributeDomain.TIER, XsdType.STRING),
        ),
    )
    freezes = 0
    original_freeze = GraphEditor.freeze

    def count_freeze(editor: GraphEditor) -> Graph:
        nonlocal freezes
        freezes += 1
        return original_freeze(editor)

    monkeypatch.setattr(GraphEditor, "freeze", count_freeze)
    result = apply_selected(
        source,
        AttributeSelector(MARK, AttributeDomain.TIER),
        lambda _editor, _node: None,
    )
    assert result == source
    assert freezes == 1


def test_budget_exhaustion_exposes_no_partial_graph() -> None:
    """The only graph visible after a bounded failure is the immutable input."""
    source = plain_graph()
    selected = NodeSet(
        source,
        tuple(Node(NodeKind.ITEM, ItemRef(TIER, index)) for index in range(3)),
    )
    calls = 0

    def remove(editor: GraphEditor, node: Node) -> None:
        nonlocal calls
        calls += 1
        editor.remove_item(item_reference(node))

    with pytest.raises(BudgetExhausted) as caught:
        apply_selected(source, selected, remove, budget=WorkBudget(steps=2))
    assert caught.value.operation == "edit.apply_selected"
    assert calls == 2
    assert len(source.tiers[0].items) == 3


def test_selector_evaluation_shares_the_bulk_budget() -> None:
    """Selection work can exhaust the common meter before callbacks begin."""
    source = plain_graph()
    calls = 0

    def count(_editor: GraphEditor, _node: Node) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(BudgetExhausted) as caught:
        apply_selected(
            source,
            ItemsSelector(TIER),
            count,
            budget=WorkBudget(steps=1),
        )
    assert caught.value.operation == "edit.apply_selected"
    assert calls == 0


def test_callback_validation_failure_exposes_no_candidate() -> None:
    """An invalid final candidate never becomes a returned graph."""
    source = plain_graph()
    selected = NodeSet(source, (Node(NodeKind.ITEM, ItemRef(TIER, 0)),))

    def invalidate(editor: GraphEditor, _node: Node) -> None:
        editor.add_relation(
            RelationInstance(
                LINK,
                ItemRef(TIER, 0),
                ItemRef(TIER, 99),
            )
        )

    with pytest.raises(GraphValidationError):
        apply_selected(source, selected, invalidate)
    assert source == plain_graph()


@pytest.mark.parametrize(
    "selected, operation",
    (
        (
            lambda graph: NodeSet(
                graph,
                (
                    Node(NodeKind.ITEM, ItemRef(TIER, 0)),
                    Node(NodeKind.ITEM, ItemRef(TIER, 1)),
                ),
            ),
            lambda editor, _node: editor.remove_items(TIER, 0, 2),
        ),
        (
            lambda graph: NodeSet(
                graph,
                (
                    Node(NodeKind.BOUNDARY, BoundaryRef(TIER, 1)),
                    Node(NodeKind.BOUNDARY, BoundaryRef(TIER, 2)),
                ),
            ),
            lambda editor, _node: editor.remove_item(ItemRef(TIER, 0)),
        ),
    ),
    ids=("item", "boundary"),
)
def test_preceding_edit_that_removes_a_later_target_refuses(
    selected: Callable[[Graph], NodeSet],
    operation: Callable[[GraphEditor, Node], object],
) -> None:
    """A departed coordinate is never redirected to its former position."""
    source = plain_graph()
    with pytest.raises(Refusal, match="removed by an earlier bulk edit"):
        apply_selected(source, selected(source), operation)
    assert source == plain_graph()


@pytest.mark.parametrize("kind", (NodeKind.RELATION_INSTANCE, NodeKind.TIER))
def test_cascade_that_removes_a_named_later_target_refuses(kind: NodeKind) -> None:
    """Static tier and declaration nodes must still exist when visited."""
    source = rich_graph()
    if kind is NodeKind.RELATION_INSTANCE:
        selected = NodeSet(
            source,
            (
                Node(NodeKind.RELATION_DECLARATION, LINK),
                Node(NodeKind.RELATION_INSTANCE, 0),
            ),
        )

        def cascade(editor: GraphEditor, node: Node) -> None:
            if node.kind is NodeKind.RELATION_INSTANCE:
                while editor.freeze().relations:
                    editor.remove_relation(0)
                declaration = next(
                    declaration
                    for declaration in source.relation_declarations
                    if declaration.name == LINK
                )
                editor.undeclare(declaration)

    else:
        selected = NodeSet(
            source,
            (
                Node(NodeKind.TIER, TIER),
                Node(NodeKind.POLYADIC_RELATION_INSTANCE, 0),
            ),
        )

        def cascade(editor: GraphEditor, node: Node) -> None:
            if node.kind is NodeKind.POLYADIC_RELATION_INSTANCE:
                while editor.freeze().relations:
                    editor.remove_relation(0)
                editor.remove_relation(PolyadicInstanceRef(0))
                editor.remove_items(TIER, 0, 3)
                declarations = {
                    declaration.name: declaration
                    for declaration in source.relation_declarations
                }
                editor.undeclare(declarations[GROUP])
                editor.undeclare(declarations[LINK])
                editor.undeclare(declarations[MEMBERS])
                editor.undeclare(source.tiers[0].declaration)

    with pytest.raises(Refusal, match="removed by an earlier bulk edit"):
        apply_selected(source, selected, cascade)


@pytest.mark.parametrize(
    "kind",
    (NodeKind.RELATION_INSTANCE, NodeKind.POLYADIC_RELATION_INSTANCE),
)
def test_departed_relation_target_refuses(kind: NodeKind) -> None:
    """Both relation coordinate spaces refuse departed selected instances."""
    source = rich_graph()
    references = (0, 1) if kind is NodeKind.RELATION_INSTANCE else (0,)
    selected = NodeSet(
        source,
        tuple(Node(kind, reference) for reference in references),
    )

    def remove_all(editor: GraphEditor, _node: Node) -> None:
        if kind is NodeKind.RELATION_INSTANCE:
            while editor.freeze().relations:
                editor.remove_relation(0)
        else:
            editor.remove_relation(PolyadicInstanceRef(0))

    if kind is NodeKind.RELATION_INSTANCE:
        with pytest.raises(Refusal, match="removed by an earlier bulk edit"):
            apply_selected(source, selected, remove_all)
    else:
        result = apply_selected(source, selected, remove_all)
        assert not result.polyadic_relations


def test_argument_shapes_refuse_before_callbacks() -> None:
    """Public argument errors are explicit rather than incidental exceptions."""
    source = plain_graph()
    with pytest.raises(TypeError, match="graph must be a Graph"):
        apply_selected(cast(Graph, object()), ItemsSelector(TIER), lambda e, n: None)
    with pytest.raises(TypeError, match="operation must be callable"):
        apply_selected(source, ItemsSelector(TIER), cast(Callable[..., object], 3))
    with pytest.raises(TypeError, match="selection must be"):
        apply_selected(source, cast(NodeSet, object()), lambda editor, node: None)


@pytest.mark.parametrize(
    "node, expected",
    (
        (Node(NodeKind.DOCUMENT, TIER), "requires coordinate type None"),
        (Node(NodeKind.TIER, None), "requires coordinate type QualifiedName"),
        (
            Node(NodeKind.RELATION_DECLARATION, None),
            "requires coordinate type QualifiedName",
        ),
        (Node(NodeKind.ITEM, None), "requires coordinate type ItemRef"),
        (Node(NodeKind.BOUNDARY, None), "requires coordinate type BoundaryRef"),
        (Node(NodeKind.RELATION_INSTANCE, None), "requires coordinate type int"),
        (
            Node(NodeKind.POLYADIC_RELATION_INSTANCE, None),
            "requires coordinate type int",
        ),
    ),
)
def test_node_kind_requires_its_coordinate_shape(node: Node, expected: str) -> None:
    """Malformed materialized nodes refuse independently of assertions."""
    source = rich_graph()
    with pytest.raises(Refusal, match=expected):
        apply_selected(source, NodeSet(source, (node,)), lambda _editor, _node: None)


@pytest.mark.parametrize(
    "node",
    (
        Node(NodeKind.TIER, QualifiedName(NAMESPACE, "absent-tier")),
        Node(
            NodeKind.RELATION_DECLARATION,
            QualifiedName(NAMESPACE, "absent-relation"),
        ),
        Node(NodeKind.ITEM, ItemRef(TIER, 99)),
        Node(NodeKind.BOUNDARY, BoundaryRef(TIER, 99)),
    ),
)
def test_node_set_coordinate_must_belong_to_input(node: Node) -> None:
    """An absent source coordinate is not reported as a departed target."""
    source = rich_graph()
    with pytest.raises(Refusal, match="does not identify a node"):
        apply_selected(source, NodeSet(source, (node,)), lambda _editor, _node: None)


@pytest.mark.parametrize(
    "node",
    (
        Node(NodeKind.RELATION_INSTANCE, 99),
        Node(NodeKind.POLYADIC_RELATION_INSTANCE, 99),
    ),
)
def test_span_relation_coordinate_must_belong_to_input(node: Node) -> None:
    """Raw span coordinates are checked before canonical node-set sorting."""
    source = rich_graph()
    spans = SpanMatches((SpanMatch(0, 0, 0, (node,), None),), Extent.EXHAUSTIVE)
    with pytest.raises(Refusal, match="does not identify a node"):
        apply_selected(source, spans, lambda _editor, _node: None)


def test_span_rejects_unknown_node_kind() -> None:
    """A future node kind cannot silently enter a relation coordinate space."""
    source = rich_graph()
    node = Node(cast(NodeKind, "future"), None)
    spans = SpanMatches((SpanMatch(0, 0, 0, (node,), None),), Extent.EXHAUSTIVE)
    with pytest.raises(Refusal, match="unsupported kind"):
        apply_selected(source, spans, lambda _editor, _node: None)


@pytest.mark.parametrize("materialized", ("nodes", "spans"))
def test_path_profile_requires_a_selector(materialized: str) -> None:
    """A path profile cannot be silently ignored after materialization."""
    source = plain_graph()
    node = Node(NodeKind.ITEM, ItemRef(TIER, 0))
    selected: NodeSet | SpanMatches
    if materialized == "nodes":
        selected = NodeSet(source, (node,))
    else:
        selected = SpanMatches((SpanMatch(0, 0, 1, (node,), None),), Extent.EXHAUSTIVE)
    with pytest.raises(Refusal, match="applies only when selection is a selector"):
        apply_selected(
            source,
            selected,
            lambda _editor, _node: None,
            path_profile=StructuralPathProfile(),
        )
