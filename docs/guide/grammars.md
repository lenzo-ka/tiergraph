# Grammars

A synchronous grammar pairs a source language with a target language. Each rule
rewrites one nonterminal into a source pattern and a target pattern at the same
time, so a derivation that recognizes a source token sequence also builds the
target sequences it translates to. tiergraph lowers a grammar into a graph,
parses token input into a chart that is itself a graph, and answers recognition,
derivation counts, and least-cost derivations by folding over that chart.
Generating target strings and the target lattice are experimental: their result
envelopes carry an `experimental` tag and may change between releases.

## Declare a grammar

A `GrammarDeclaration` names its nonterminals, its start symbol, and its rules.
A `GrammarRule` has a left-hand nonterminal, a source pattern, and a target
pattern. A pattern is a tuple of `GrammarTerminal` values, which carry a token
as an XSD string, and `GrammarHole` values, which bind a named variable to a
nonterminal. The target may reorder, drop, or add terminals, and it must use
exactly the variables the source binds, plus any the rule declares in
`awaited_variables`. A rule's `weight` is an `xsd:decimal` cost; a rule without
one costs `1`, and the cost of a derivation is the sum of its rules' costs.

This grammar reads `$ 10` and says it two ways, preferring `ten dollars`.

```python
import json

from tiergraph import (
    AttributeValue,
    GrammarDeclaration,
    GrammarHole,
    GrammarRule,
    GrammarTerminal,
    QualifiedName,
    XsdType,
    best,
    count,
    generate,
    grammar_loads,
    lower_grammar,
    recognize,
)

ns = "urn:example:grammar"


def q(local: str) -> QualifiedName:
    return QualifiedName(ns, local)


def word(text: str) -> GrammarTerminal:
    return GrammarTerminal(AttributeValue(q("text"), XsdType.STRING, text))


def hole(variable: str, nonterminal: QualifiedName) -> GrammarHole:
    return GrammarHole(
        AttributeValue(q("variable"), XsdType.STRING, variable), nonterminal
    )


def cost(lexical: str) -> AttributeValue:
    return AttributeValue(q("weight"), XsdType.DECIMAL, lexical)


sentence, number = q("S"), q("N")
money = GrammarDeclaration(
    (sentence, number),
    sentence,
    (
        GrammarRule(
            sentence,
            (word("$"), hole("n", number)),
            (hole("n", number), word("dollars")),
        ),
        GrammarRule(number, (word("10"),), (word("ten"),), weight=cost("0.5")),
        GrammarRule(
            number, (word("10"),), (word("one"), word("zero")), weight=cost("2")
        ),
    ),
)
```

## Recognize, count, and choose

`lower_grammar` turns the declaration into a `LoweredGrammar`, a graph built by
the [build machine](construction.md#the-build-machine). `recognize` parses a
token sequence against it into a `ParseForest`, whose `recognized()` says
whether the start symbol spans the whole input. `count` returns the number of
derivations, and `best` returns the lowest-cost derivations as `BestDerivation`
values, each with its total cost and a witness path through the chart.

```python
lowered = lower_grammar(money)
print("recognized:", recognize(lowered, ("$", "10")).recognized())
print("reversed:", recognize(lowered, ("10", "$")).recognized())
print("derivations:", count(lowered, ("$", "10")))
print("costs:", [derivation.weight for derivation in best(lowered, ("$", "10"), 2)])
```

```text
recognized: True
reversed: False
derivations: 2
costs: ['1.5', '3.0']
```

The cheaper derivation costs `1 + 0.5`: the unweighted sentence rule and the
`ten` rule.

For a fixed grammar whose longest source pattern has `m` elements, recognition
takes time polynomial in the input length `n`, at most on the order of
`n^(m+1)`. A `ParseForest` can be built once and passed to `count`, `best`, and
`generate` in place of the lowered grammar and tokens; build it with
`recognize(..., collapse_units=False)` when it will be used for generation.

## Generate target strings

`generate` materializes the target side of the best derivations as a
`GenerationResult`. Each `GeneratedDerivation` carries its cost, its target
tokens, the rule applications that produced it, and the source span each target
piece came from.

```python
generated = generate(lowered, ("$", "10"), count=2)
for derivation in generated.derivations:
    print(derivation.weight, derivation.text)
print("truncated:", generated.truncated)
```

```text
1.5 ten dollars
3.0 one zero dollars
truncated: False
```

When the input is a `GrammarInput` rather than plain symbols, each token carries
one or more `Realization` alternatives: the target tokens to emit wherever a
rule copies that source symbol to the target, each with an optional weight.
`target_lattice` returns every target alternative of a forest as a
`TargetLattice` rather than a ranked list.

## The grammar JSON format

`GrammarDeclaration.to_data()` writes a grammar as strict JSON, and
`grammar_loads` reads it back. The document is an object with `nonterminals`
(an array of qualified names), `start`, and `rules`. A qualified name is an
object with `namespace` and `local_name`. A scalar value, such as a terminal's
text, a hole's variable, a weight, or a boundary, is an attribute record with
`name`, `value_type`, and `lexical`. Each rule has every field present:

```python
print(grammar_loads(json.dumps(money.to_data())) == money)
print(json.dumps(money.rules[1].to_data(), indent=2))
```

```text
True
{
  "left": {
    "namespace": "urn:example:grammar",
    "local_name": "N"
  },
  "source": [
    {
      "kind": "terminal",
      "text": {
        "name": {
          "namespace": "urn:example:grammar",
          "local_name": "text"
        },
        "value_type": "string",
        "lexical": "10"
      }
    }
  ],
  "target": [
    {
      "kind": "terminal",
      "text": {
        "name": {
          "namespace": "urn:example:grammar",
          "local_name": "text"
        },
        "value_type": "string",
        "lexical": "ten"
      }
    }
  ],
  "boundary": {
    "name": {
      "namespace": "urn:tiergraph:grammar",
      "local_name": "boundary"
    },
    "value_type": "string",
    "lexical": "complete"
  },
  "awaited_variables": [],
  "weight": {
    "name": {
      "namespace": "urn:example:grammar",
      "local_name": "weight"
    },
    "value_type": "decimal",
    "lexical": "0.5"
  }
}
```

A source or target element is `{"kind": "terminal", "text": ...}` or
`{"kind": "hole", "variable": ..., "nonterminal": ...}`. `weight` is `null` for
the unit cost. `boundary` is `COMPLETE_BOUNDARY` unless a rule declares
otherwise, and `provenance`, an optional array of string attribute records, is
written only when present. The reader refuses unknown and missing fields.

## From the command line

The `tiergraph grammar` commands read a grammar document from a file or stdin.
`recognize`, `count`, and `best` take the input as a JSON array of tokens.
Saved as `money.json`, the grammar above answers:

```console
$ tiergraph grammar recognize money.json --tokens-json '["$", "10"]'
{
  "recognized": true
}
$ tiergraph grammar count money.json --tokens-json '["$", "10"]'
{
  "count": 2
}
```

`best --count N` prints the lowest-cost derivations, and `recognize --forest`
prints the whole parse forest. `generate` and `lattice` take a typed input,
the JSON form of `GrammarInput`, with `--input-json`:

```console
$ tiergraph grammar generate money.json --count 1 --input-json \
    '{"tokens": [{"symbol": "$", "realization": [{"tokens": ["$"]}]},
                 {"symbol": "10", "realization": [{"tokens": ["10"]}]}]}'
```

Its output is the `GenerationResult` envelope, whose derivations carry `text`,
`tokens`, `weight`, and their rule applications. Every grammar command except
`lattice` accepts `--max-steps`; see [Work budgets](work-budgets.md).
