"""Address a small multi-ring mix graph through the generic TG-PATH surface.

The ``/mix/{ring}/...`` segment vocabulary is illustrative, not a claim that a
ring belongs in the eventual mix coordinate shape.  Ring ownership and profile
provenance remain open design questions; this example makes both explicit in a
snapshot-owned profile.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tiergraph import (
    AlternativeRef,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryBinding,
    BoundaryRef,
    CanonicalPath,
    Graph,
    Item,
    ItemBinding,
    ItemRef,
    NamespaceDeclaration,
    PathBinding,
    PathKind,
    PathOffender,
    PathRefusal,
    PathRefusalCode,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    ResolvedAlternative,
    ResolvedBoundary,
    ResolvedItem,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    resolve_path,
)

MIX_NAMESPACE = "https://tiergraph.dev/examples/mix-paths/mix"
CLOCK_NAMESPACE = "https://tiergraph.dev/examples/mix-paths/clock"
MIDI_NAMESPACE = "https://tiergraph.dev/examples/mix-paths/midi"
ARRANGEMENT = QualifiedName(MIX_NAMESPACE, "arrangement")
STEM = QualifiedName(MIX_NAMESPACE, "stem")
BUS = QualifiedName(MIX_NAMESPACE, "bus")
CLOCK = QualifiedName(CLOCK_NAMESPACE, "clock")
NOTE = QualifiedName(MIDI_NAMESPACE, "note")
VOICING = QualifiedName(MIDI_NAMESPACE, "voicing")
PITCH = QualifiedName(MIDI_NAMESPACE, "pitch")
VELOCITY = QualifiedName(MIDI_NAMESPACE, "velocity")
ONSET = QualifiedName(MIDI_NAMESPACE, "onset")
DURATION = QualifiedName(MIDI_NAMESPACE, "duration")
CHANNEL = QualifiedName(MIDI_NAMESPACE, "channel")
TEMPO = QualifiedName(MIDI_NAMESPACE, "tempo")
DIVISION = QualifiedName(MIDI_NAMESPACE, "division")
TITLE = "Tropic of Capricorn"

_CANONICAL_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def build_graph() -> Graph:
    """Build a mix diamond whose two MIDI voicings share a clock."""
    bus_type = QualifiedName(MIX_NAMESPACE, "bus-type")
    stem_type = QualifiedName(MIX_NAMESPACE, "stem-type")
    bus_membership = SimpleRelationDeclaration(
        QualifiedName(MIX_NAMESPACE, "buses"), BUS, bus_type
    )
    stem_membership = SimpleRelationDeclaration(
        QualifiedName(MIX_NAMESPACE, "stems"), STEM, stem_type
    )
    arrangement = BipartiteRelationDeclaration(ARRANGEMENT, bus_type, stem_type)
    reconverges = BipartiteRelationDeclaration(
        QualifiedName(MIX_NAMESPACE, "reconverges"), stem_type, bus_type
    )
    voicing = PolyadicRelationDeclaration(
        VOICING,
        RelationSideDeclaration((RelationEndpointKind.ITEM,), (STEM,), 1, 1),
        RelationSideDeclaration((RelationEndpointKind.ITEM,), (NOTE,), 26, 26),
        unique_sources=True,
    )
    start = ItemRef(BUS, 0)
    out = ItemRef(BUS, 1)
    bed = ItemRef(STEM, 0)
    sting = ItemRef(STEM, 1)

    def note(
        identifier: str,
        pitch: int,
        onset: int,
        duration: int,
        channel: int,
        velocity: int,
    ) -> Item:
        return Item(
            identifier,
            (
                AttributeValue(CHANNEL, XsdType.INTEGER, str(channel)),
                AttributeValue(DURATION, XsdType.INTEGER, str(duration)),
                AttributeValue(ONSET, XsdType.INTEGER, str(onset)),
                AttributeValue(PITCH, XsdType.INTEGER, str(pitch)),
                AttributeValue(VELOCITY, XsdType.INTEGER, str(velocity)),
            ),
        )

    landing_tick = 7680
    triplet_onsets = (0, 640, 1280, 1920, 3200, 3840, 4480, 5760, 6400, 7040)
    quarter_onsets = (
        0,
        480,
        960,
        1440,
        1920,
        2880,
        3360,
        3840,
        4320,
        4800,
        5760,
        6240,
        6720,
        7200,
    )
    straight_triplets = (60, 64, 67, 65, 69, 72, 71, 67, 64, 62)
    turning_triplets = (60, 64, 67, 62, 65, 69, 68, 65, 64, 62)
    straight_quarters = (48, 55, 52, 57, 53, 60, 55, 59, 57, 53, 55, 52, 50, 55)
    turning_quarters = (48, 55, 52, 57, 50, 57, 53, 56, 55, 52, 55, 52, 50, 55)
    triplet_velocities = (62, 68, 72, 76, 82, 88, 96, 90, 82, 74)
    quarter_velocities = (48, 54, 58, 62, 60, 68, 72, 78, 84, 92, 98, 88, 78, 70)

    def arrangement_notes(
        label: str, triplets: tuple[int, ...], quarters: tuple[int, ...]
    ) -> tuple[Item, ...]:
        voice_a = tuple(
            note(
                f"{label}-three-{index}",
                pitch,
                onset,
                following - onset + 40,
                0,
                velocity,
            )
            for index, (pitch, onset, following, velocity) in enumerate(
                zip(
                    triplets,
                    triplet_onsets,
                    (*triplet_onsets[1:], landing_tick),
                    triplet_velocities,
                    strict=True,
                )
            )
        )
        voice_b = tuple(
            note(
                f"{label}-four-{index}",
                pitch,
                onset,
                following - onset + 30,
                1,
                velocity,
            )
            for index, (pitch, onset, following, velocity) in enumerate(
                zip(
                    quarters,
                    quarter_onsets,
                    (*quarter_onsets[1:], landing_tick),
                    quarter_velocities,
                    strict=True,
                )
            )
        )
        landing = (
            note(f"{label}-landing-left", 60, landing_tick, 960, 0, 66),
            note(f"{label}-landing-right", 60, landing_tick, 960, 1, 66),
        )
        return voice_a + voice_b + landing

    straight = arrangement_notes("south", straight_triplets, straight_quarters)
    turning = arrangement_notes("turn", turning_triplets, turning_quarters)
    notes = straight + turning
    return Graph(
        (
            NamespaceDeclaration("clock", CLOCK_NAMESPACE),
            NamespaceDeclaration("midi", MIDI_NAMESPACE),
            NamespaceDeclaration("mix", MIX_NAMESPACE),
        ),
        (
            Tier(TierDeclaration(STEM, "Mix stems"), (Item("bed"), Item("sting"))),
            Tier(TierDeclaration(BUS, "Mix buses"), (Item("start"), Item("out"))),
            Tier(
                TierDeclaration(CLOCK, "Shared clock"),
                tuple(Item(f"tick-{index}") for index in range(4)),
            ),
            Tier(TierDeclaration(NOTE, "MIDI note events"), notes),
        ),
        (bus_membership, stem_membership, arrangement, reconverges, voicing),
        (
            RelationInstance(ARRANGEMENT, start, sting),
            RelationInstance(ARRANGEMENT, start, bed),
            RelationInstance(reconverges.name, bed, out),
            RelationInstance(reconverges.name, sting, out),
        ),
        (
            AttributeDeclaration(DURATION, AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(CHANNEL, AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(ONSET, AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(PITCH, AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(VELOCITY, AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(TEMPO, AttributeDomain.DOCUMENT, XsdType.INTEGER),
            AttributeDeclaration(DIVISION, AttributeDomain.DOCUMENT, XsdType.INTEGER),
        ),
        attributes=(
            AttributeValue(DIVISION, XsdType.INTEGER, "480"),
            AttributeValue(TEMPO, XsdType.INTEGER, "500000"),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                VOICING,
                (bed,),
                tuple(ItemRef(NOTE, index) for index in range(len(straight))),
            ),
            PolyadicRelationInstance(
                VOICING,
                (sting,),
                tuple(
                    ItemRef(NOTE, index) for index in range(len(straight), len(notes))
                ),
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class MixPathProfile:
    """Interpret mix items, shared-clock boundaries, and arrangements in one snapshot."""

    graph: Graph

    def _snapshot(self, graph: Graph, path: CanonicalPath | None = None) -> None:
        if graph is not self.graph:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text="" if path is None else str(path),
                    path=path,
                    profile_reason="different_mix_snapshot",
                ),
            )

    def bind(self, path: CanonicalPath, graph: Graph) -> PathBinding:
        """Bind one of the three mix-domain path forms."""
        self._snapshot(graph, path)
        segments = path.segments
        if len(segments) == 4 and segments[0] == "mix":
            namespace = {"mix": MIX_NAMESPACE, "midi": MIDI_NAMESPACE}.get(segments[1])
            if namespace is None:
                raise PathRefusal(
                    PathRefusalCode.UNKNOWN_FORM,
                    PathOffender(text=str(path), path=path),
                )
            return ItemBinding(
                ItemRef(
                    QualifiedName(namespace, segments[2]),
                    _index(segments[3], 3, path),
                )
            )
        if len(segments) == 3 and segments[:2] == ("mix", "clock"):
            return BoundaryBinding(BoundaryRef(CLOCK, _index(segments[2], 2, path)))
        if len(segments) == 3 and segments[:2] == ("mix", "arrangement"):
            return AlternativeRef(
                ItemRef(BUS, 0), ARRANGEMENT, _index(segments[2], 2, path)
            )
        raise PathRefusal(
            PathRefusalCode.UNKNOWN_FORM, PathOffender(text=str(path), path=path)
        )

    def spell(self, binding: PathBinding, graph: Graph) -> CanonicalPath:
        """Spell a supported binding after validating its mix provenance."""
        self._snapshot(graph)
        if isinstance(binding, ItemBinding) and isinstance(binding.reference, ItemRef):
            reference = binding.reference
            rings = {MIX_NAMESPACE: "mix", MIDI_NAMESPACE: "midi"}
            ring = rings.get(reference.tier.namespace)
            if ring is not None and reference.tier in (STEM, BUS, NOTE):
                return CanonicalPath(
                    ("mix", ring, reference.tier.local_name, str(reference.index))
                )
        elif isinstance(binding, BoundaryBinding) and isinstance(
            binding.reference, BoundaryRef
        ):
            if binding.reference.tier == CLOCK:
                return CanonicalPath(("mix", "clock", str(binding.reference.index)))
        elif isinstance(binding, AlternativeRef):
            if binding.owner == ItemRef(BUS, 0) and binding.relation == ARRANGEMENT:
                return CanonicalPath(("mix", "arrangement", str(binding.index)))
        raise PathRefusal(
            PathRefusalCode.UNSPELLABLE,
            PathOffender(text="", profile_reason="unsupported_mix_binding"),
        )

    def alternatives(
        self, owner: ItemRef, relation: QualifiedName, graph: Graph
    ) -> tuple[object, ...]:
        """Return ordered note voicings by a stable stem coordinate key."""
        self._snapshot(graph)
        if relation != ARRANGEMENT:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text="", relation=relation, profile_reason="unsupported_relation"
                ),
            )
        if owner != ItemRef(BUS, 0):
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text="", tier=owner.tier, profile_reason="unsupported_owner"
                ),
            )
        stems = (
            cast(ItemRef, edge.right)
            for edge in graph.relations
            if edge.declaration == ARRANGEMENT and edge.left == owner
        )
        return tuple(
            next(
                instance.targets
                for instance in graph.polyadic_relations
                if instance.declaration == VOICING and instance.sources == (stem,)
            )
            for stem in sorted(stems, key=lambda reference: reference.index)
        )


def _index(value: str, segment_index: int, path: CanonicalPath) -> int:
    if _CANONICAL_INDEX.fullmatch(value):
        return int(value)
    body = value[1:] if value.startswith("+") else value
    code = (
        PathRefusalCode.NONCANONICAL_SEGMENT
        if body and body.isdecimal()
        else PathRefusalCode.INVALID_SEGMENT
    )
    raise PathRefusal(
        code,
        PathOffender(
            text=str(path),
            path=path,
            segment_index=segment_index,
            segment=value,
        ),
    )


def _attribute_int(graph: Graph, reference: ItemRef, name: QualifiedName) -> int:
    item = next(
        tier.items[reference.index]
        for tier in graph.tiers
        if tier.declaration.name == reference.tier
    )
    return int(next(value.lexical for value in item.attributes if value.name == name))


def _document_int(graph: Graph, name: QualifiedName) -> int:
    return int(next(value.lexical for value in graph.attributes if value.name == name))


def _vlq(value: int) -> bytes:
    """Encode one non-negative MIDI variable-length quantity."""
    if value < 0:
        raise ValueError("MIDI VLQ cannot encode a negative value")
    if value > 0x0FFFFFFF:
        raise ValueError("MIDI VLQ cannot exceed four bytes")
    encoded = [value & 0x7F]
    value >>= 7
    while value:
        encoded.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(encoded))


def render_midi(
    graph: Graph, profile: MixPathProfile, arrangement_index: int = 0
) -> bytes:
    """Render one addressed arrangement as a playable format-0 MIDI file."""
    selected = resolve_path(
        graph,
        profile,
        f"/mix/arrangement/{arrangement_index}",
        require=PathKind.ALTERNATIVE,
    )
    assert isinstance(selected, ResolvedAlternative)
    notes = cast(tuple[ItemRef, ...], selected.value)
    division = _document_int(graph, DIVISION)
    tempo = _document_int(graph, TEMPO)
    if not 1 <= division <= 0x7FFF:
        raise ValueError("MIDI division must be between 1 and 32767")
    if not 1 <= tempo <= 0xFFFFFF:
        raise ValueError("MIDI tempo must fit three bytes")

    events: list[tuple[int, int, int, bytes]] = []
    note_channels = {
        reference: _attribute_int(graph, reference, CHANNEL) for reference in notes
    }
    if any(not 0 <= channel <= 15 for channel in note_channels.values()):
        raise ValueError("MIDI channel is out of range")
    channels = tuple(sorted(set(note_channels.values())))
    channel_notes = {
        channel: tuple(
            reference for reference in notes if note_channels[reference] == channel
        )
        for channel in channels
    }
    note_ranks = {
        reference: rank
        for references in channel_notes.values()
        for rank, reference in enumerate(references)
    }
    for channel in channels:
        for step, value in enumerate((48, 60, 74, 90, 108, 116, 108, 98, 86)):
            events.append((step * 960, 0, channel, bytes((0xB0 | channel, 7, value))))

    for ordinal, reference in enumerate(notes):
        pitch = _attribute_int(graph, reference, PITCH)
        velocity = _attribute_int(graph, reference, VELOCITY)
        onset = _attribute_int(graph, reference, ONSET)
        duration = _attribute_int(graph, reference, DURATION)
        channel = note_channels[reference]
        if not 0 <= pitch <= 127 or not 1 <= velocity <= 127:
            raise ValueError("MIDI note pitch or velocity is out of range")
        if onset < 0 or duration <= 0:
            raise ValueError("MIDI note timing must be non-negative with duration")
        rank = note_ranks[reference]
        last_rank = len(channel_notes[channel]) - 1
        if channel == 0:
            if rank % 2 == 0:
                amplitude = round(64 * (last_rank - rank) / last_rank)
                pan = 64 - amplitude
            else:
                amplitude = round(63 * (last_rank - rank) / (last_rank - 1))
                pan = 64 + amplitude
            pan_priority = 3
            note_priority = 4
        else:
            pan = 64 + round(63 * (last_rank - rank) / last_rank)
            pan_priority = 1
            note_priority = 2
        events.append((onset, pan_priority, ordinal, bytes((0xB0 | channel, 10, pan))))
        events.append(
            (onset, note_priority, ordinal, bytes((0x90 | channel, pitch, velocity)))
        )
        events.append((onset + duration, 1, ordinal, bytes((0x80 | channel, pitch, 0))))

    title = TITLE.encode("ascii")
    track = bytearray(b"\x00\xff\x03" + _vlq(len(title)) + title)
    track.extend(b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big"))
    previous_tick = 0
    for tick, _priority, _ordinal, message in sorted(events):
        track.extend(_vlq(tick - previous_tick))
        track.extend(message)
        previous_tick = tick
    track.extend(b"\x00\xff\x2f\x00")
    header = b"MThd" + (6).to_bytes(4, "big") + bytes((0, 0, 0, 1))
    header += division.to_bytes(2, "big")
    return header + b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)


def run_example() -> dict[str, object]:
    """Resolve all three path kinds and return arrangement zero's MIDI bytes."""
    graph = build_graph()
    profile = MixPathProfile(graph)
    item = resolve_path(graph, profile, "/mix/midi/note/0", require=PathKind.ITEM)
    boundary = resolve_path(graph, profile, "/mix/clock/2", require=PathKind.POSITION)
    alternative = resolve_path(
        graph, profile, "/mix/arrangement/0", require=PathKind.ALTERNATIVE
    )
    assert isinstance(item, ResolvedItem)
    assert isinstance(boundary, ResolvedBoundary)
    assert isinstance(alternative, ResolvedAlternative)
    path = alternative.value
    assert isinstance(path, tuple)
    return {
        "item": item.current.to_data(),
        "position": boundary.current.to_data(),
        "arrangement": [reference.to_data() for reference in path],
        "midi": render_midi(graph, profile),
    }


def main(argv: list[str] | None = None) -> int:
    """Write arrangement zero to the requested path, defaulting to ``mix.mid``."""
    arguments = sys.argv[1:] if argv is None else argv
    output = Path(arguments[0]) if arguments else Path("mix.mid")
    graph = build_graph()
    output.write_bytes(render_midi(graph, MixPathProfile(graph)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
