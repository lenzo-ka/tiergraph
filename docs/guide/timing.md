# Timing

`ClockProfile` refines a declared clock tier and maps boundaries on other tiers
to clock positions. Positions use a tick and gap, so inserted boundaries do not
require renumbering existing ticks. `anchored_position()` resolves a graph
boundary through the profile.

`PhysicalTiming` may store physical values or derive them from a rate. The
profile checks monotonicity, graph identity, and binding coverage when it is
constructed.
