# Construction

Direct constructors create an immutable value in one step. Declare namespaces,
tiers, attributes, and relations before referring to them. `Graph` performs the
cross-object validation.

The build machine represents an ordered edit stream with `Program` and command
values such as `DeclareTier`, `AddItem`, `AttachValue`, and `Relate`.
`Program.unroll()` returns `AsBuilt`, including promoted references; `execute()`
is the convenience operation that returns its `Graph`. A
failed command raises `ExecutionError` with its program location. `Repeat` has a
declared upper bound so a serialized program cannot request unbounded expansion.
