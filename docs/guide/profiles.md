# Profiles

Profiles assign a checked role to ordinary graph declarations.

`OrderedRootsProfile` combines stored root order with roots inferred from a
dependency relation. `PersistedChoiceProfile` reads candidates and an optional
stored default. `JsonValueProfile` represents recursive JSON as items and
ordered polyadic relations while keeping scalar leaves as attributes.

Profiles do not change the graph kernel or its wire node kinds. Constructing a
profile checks the declarations needed by that interpretation.
