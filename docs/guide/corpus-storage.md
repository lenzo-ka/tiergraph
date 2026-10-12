# Store a versioned corpus

Use `tiergraph.tgdb` when a collection of graphs needs ordered identity,
atomic passes, retained versions, and bounded queries. The store lives in one
local directory. SQLite is the publication point, while graph documents,
patches, and payload bytes share a content-addressed object pool. Import tgdb
explicitly; importing `tiergraph` alone does not load tgdb or SQLite.

A version is always a complete graph document. Loading a head never replays
its history. The patch recorded beside a later version is for audit,
interchange, and review. It is replay-checked against the previous complete
document before the version can commit.

## Run a corpus loop

The example below uses domain-general records and units. It follows the same
shape as a corpus workflow: create the corpus, impute missing values, reconcile
automatic results, preserve a manual correction, check for a fixpoint, and
find the next record that needs work. It does not assume a fixed store location.

```python
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    EditAnnotations,
    Graph,
    Item,
    ItemRef,
    Journal,
    NamespaceDeclaration,
    QualifiedName,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.tgdb import CommitReceipt, Query, TgdbStore

NAMESPACE = "https://tiergraph.dev/examples/corpus-storage"
UNITS = QualifiedName(NAMESPACE, "units")
UNIT_TYPE = QualifiedName(NAMESPACE, "unit")
MEMBERSHIP = QualifiedName(NAMESPACE, "unit-membership")
STATE = QualifiedName(NAMESPACE, "state")


def record_graph(name: str) -> Graph:
    """Return one record with an observed unit and one missing state."""
    return Graph(
        (NamespaceDeclaration("corpus", NAMESPACE),),
        (
            Tier(
                TierDeclaration(UNITS, "Units"),
                (
                    Item(
                        f"{name}-unit-0",
                        (AttributeValue(STATE, XsdType.STRING, "observed"),),
                    ),
                    Item(f"{name}-unit-1"),
                ),
            ),
        ),
        (SimpleRelationDeclaration(MEMBERSHIP, UNITS, UNIT_TYPE),),
        attribute_declarations=(
            AttributeDeclaration(STATE, AttributeDomain.ITEM, XsdType.STRING),
        ),
    )


def unit_state(graph: Graph, index: int) -> str | None:
    """Return one unit's workflow state, if it has one."""
    tier = next(tier for tier in graph.tiers if tier.declaration.name == UNITS)
    return next(
        (
            value.lexical
            for value in tier.items[index].attributes
            if isinstance(value, AttributeValue) and value.name == STATE
        ),
        None,
    )


def impute(graph: Graph) -> Graph:
    """Fill the one missing state without changing an existing value."""
    if unit_state(graph, 1) is not None:
        return graph
    return graph.set_attribute(
        ItemRef(UNITS, 1), AttributeValue(STATE, XsdType.STRING, "estimated")
    )


def reconcile(graph: Graph) -> Graph:
    """Accept an estimate while leaving other states untouched."""
    if unit_state(graph, 1) != "estimated":
        return graph
    return graph.set_attribute(
        ItemRef(UNITS, 1), AttributeValue(STATE, XsdType.STRING, "reconciled")
    )


def publish_pass(
    store: TgdbStore,
    stage: str,
    iteration: int,
    transform: Callable[[Graph], Graph],
) -> CommitReceipt:
    """Stage one transformed graph at a time and publish the pass atomically."""
    annotations = EditAnnotations(stage=stage, iteration=iteration)
    with store.write(annotations=annotations) as transaction:
        with store.snapshot() as snapshot:
            for handle in snapshot.run(Query.all("corpus")):
                transaction.publish(
                    handle.instance_uid,
                    transform(handle.load()),
                    expected=handle.seq,
                    patch=None,
                    annotations=annotations,
                )
        return transaction.commit()


with TemporaryDirectory() as directory:
    store_path = Path(directory) / "corpus.tgdb"
    with TgdbStore.create(store_path) as store:
        with store.write() as transaction:
            corpus = transaction.create_collection("corpus")
            transaction.create_instance(corpus, "record-a", record_graph("record-a"))
            transaction.create_instance(corpus, "record-b", record_graph("record-b"))
            transaction.declare_index("workflow-state", UNITS, STATE)
            created = transaction.commit()
        print(f"created {len(created.versions)} records")

        imputed = publish_pass(store, "impute", 1, impute)
        print(f"impute changed {len(imputed.versions)} records")

        reconciled = publish_pass(store, "reconcile", 2, reconcile)
        print(f"reconcile changed {len(reconciled.versions)} records")

        head = store.get("record-b", collection="corpus")
        journal = Journal(stage="hand-correct", iteration=3)
        editor = head.load().edit(journal)
        editor.set_attribute(
            ItemRef(UNITS, 1),
            AttributeValue(STATE, XsdType.STRING, "corrected"),
        )
        annotations = EditAnnotations(stage="hand-correct", iteration=3)
        with store.write(annotations=annotations) as transaction:
            transaction.publish(
                head.instance_uid,
                editor.freeze(),
                expected=head.seq,
                patch=journal.to_patch(),
                annotations=annotations,
            )
            corrected = transaction.commit()
        print(f"hand-corrected {corrected.versions[0].name}")

        baseline = corrected.commit_seq
        assert baseline is not None
        repeated = publish_pass(store, "reconcile", 4, reconcile)
        assert repeated.commit_seq is None
        changed = store.query(Query.all("corpus").changed_since(baseline))
        print(f"fixpoint changed {len(changed)} records")

        needs_work = store.query(
            Query.all("corpus").indexed(
                "workflow-state",
                AttributeValue(STATE, XsdType.STRING, "reconciled"),
            )
        )
        print("next to fix: " + ", ".join(handle.name for handle in needs_work))
```
```text
created 2 records
impute changed 2 records
reconcile changed 2 records
hand-corrected record-b
fixpoint changed 0 records
next to fix: record-a
```

The creation transaction publishes the ordered collection, both instances,
their initial versions, and the index declaration together. Each later
automatic pass reads and stages one graph at a time. `commit()` is still one
publication point, so readers see either every old head or every new head.

`publish()` always skips byte-identical documents. The repeated reconcile pass
therefore creates no commit, and `changed_since()` confirms the functional
fixpoint from indexed fingerprints without loading a document. The manual edit
passes `Journal.to_patch()` so the store can retain the exact, replay-verified
transition. Because `reconcile()` changes only an estimate, running it again
does not overwrite the corrected value.

The `workflow-state` declaration is an opt-in per-item index. Its equality
query narrows the corpus without opening graph documents, and results stay in
declared collection and instance order. Use `where=` only for a condition that
cannot be expressed by the derived or declared indexes; that path loads each
survivor separately.

## Keep identity, order, and resources explicit

Collection and instance IDs are stable and separate from their names and
positions. Positions are the presented order; renaming, moving, or publishing
a new version does not replace an ID. Query results use declared order unless
`order_by=` explicitly requests a supported order key.

External resources remain ordinary ordered graph content. Each resource item
and attachment has a durable ID and a declared position distinct from the
payload digest. Two resource items may therefore share bytes without sharing
identity. Structured text, nested key-value data, binary media, and attached
graphs keep their own media type and schema. A resource may attach to another
resource, and several attachments keep their declared order and roles.

Payloads are stored and resolved by digest. Type-specific metadata belongs on
the resource item, where a declared blob column may index it. Inspectors are
optional and explicit: opening, loading, querying, publishing, and importing do
not run one. An inspection reads a held payload without mutating graph content,
but it records derived facts and therefore needs a writable store. Publishing
selected inspected facts is a separate versioned edit.

## Operate the store deliberately

Use a short-lived `snapshot()` when several reads must see the same heads. A
snapshot holds back WAL checkpointing while it remains open. Open a store with
`mode="ro"` for read-only work and `mode="rw"` for publication.

tgdb supports local filesystems. Use `backup()` or `tiergraph tgdb backup` for a
consistent copy of a live store; copying only `catalog.sqlite3` can omit
committed WAL state. Integrity checking, reindexing, repacking, migration, and
garbage collection are explicit operations. Garbage collection and pruning
report without deleting unless their destructive option is requested.

The [generated API reference](../reference/api.md#tiergraphtgdb) describes the
complete `tiergraph.tgdb` surface. The
[generated CLI reference](../reference/cli.md#tiergraph-tgdb) lists every store
command, option, example, and exit status.
