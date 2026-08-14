## tiergraph

Attribute values are scalar XSD-typed values. Recursive JSON values are represented
by `JsonValueProfile` as ordinary items and explicitly ordered polyadic relations;
their scalar leaves remain canonical attributes. This adds no kernel kind and does
not pack containment or order into a lexical string. Provenance is a separate
declared relation from a value root to an ordinary source item, so provenance can
itself form a chain.
