## tiergraph

Attribute values are scalar XSD-typed values. Recursive JSON values are represented
by `JsonValueProfile` as ordinary items and explicitly ordered polyadic relations;
their scalar leaves remain canonical attributes. This adds no kernel kind and does
not pack containment or order into a lexical string. The value profile does not
define or validate provenance; applications may declare their own relations for it.

`tiergraph` represents ordered tiers, typed items and declared relations. The
optional clock profile aligns tier boundaries to refined `(tick, gap)`
positions and exposes independently stored or rate-derived physical timing.

### Graphviz DOT

The companion `tiergraph_dot` package renders a graph through public API only:

```python
import tiergraph_dot

dot = tiergraph_dot.dumps(graph)
dot_with_clock = tiergraph_dot.dumps(graph, clock=clock_profile)
```

Tier and item rows follow graph order. Bipartite instances and polyadic
instances retain their respective declared orders. With a clock profile, every
refined clock boundary is emitted, timed events align to their bound starts,
and extents terminate at their bound ends. Untimed tiers retain a separate
structural axis. Item labels contain durable ids, declared attributes and any
physical timing exposed by the profile; the renderer does not interpret domain
attributes such as phones or spellings.

Empty tiers are omitted unless `include_empty_tiers=True`. A relation endpoint
on an omitted empty tier is refused and names that endpoint; enabling empty
tiers renders it. A clock profile for a different graph is also refused rather
than producing partial output.

Ordered roots and persisted default choices are profile roles over ordered
polyadic relations. `OrderedRootsProfile` reconciles stored root membership with
roots inferred from declared dependency relations while preserving stored target
order. `PersistedChoiceProfile` requires a source-unique, distinct candidate
relation and a source-unique singleton subset relation for an optional default.
