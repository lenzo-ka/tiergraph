# JSON format

The wire representation is strict JSON: object keys are strings, arrays retain
order, and scalar attribute values retain their declared XSD type and canonical
lexical form. The top-level document carries `FORMAT_VERSION`.

Implementers should treat qualified names, declaration order, tier order, item
order, relation endpoint order, and boundary indexes as data. Validate all
references after decoding. Do not infer relation meaning from a name or compact
ordered relations into unordered sets.

The generated schema in `schema/tiergraph.schema.json` describes structural
shape. The Python decoder remains the authority for semantic constraints such
as declaration compatibility, acyclicity, and reference validity. A format
version decision accompanies changes to the generated schema or declaration
shape.
