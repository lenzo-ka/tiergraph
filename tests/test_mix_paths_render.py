"""Structural witnesses for the MIDI file the mix-paths example renders.

`examples/mix_paths.py` addresses a small mix graph through the generic path
surface and renders one addressed arrangement as a standard MIDI file.  What
this module checks is that file -- that its chunk framing is well formed and
self-consistent, that the render is reproducible, and that every note in it is
reconstructible from the graph items the path selected.

It does not check that the result is music.  Nothing here plays audio, drives a
synthesizer, or holds any opinion about whether the arrangement sounds like
anything; a render that is structurally perfect and musically dead passes every
assertion below.  The boundary is worth writing down because the assertions are
detailed enough to be mistaken for more than they are.  This is a structural and
derivational witness, not a perceptual one: the checks establish that the file
says what the graph says, and whether the graph says anything worth hearing is a
question none of them asks.

The parser below is deliberately its own reader rather than a call back into the
renderer's helpers.  A witness written from the code under test restates it; a
witness written from the file format disagrees with it when the renderer is
wrong, which is the only time a test is worth anything.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from examples.mix_paths import (
    CHANNEL,
    DURATION,
    ONSET,
    PITCH,
    VELOCITY,
    MixPathProfile,
    build_graph,
    main,
    render_midi,
)

from tiergraph import (
    AttributeValue,
    Graph,
    Item,
    ItemBinding,
    ItemRef,
    PathKind,
    QualifiedName,
    ResolvedAlternative,
    Tier,
    resolve_path,
)

ROOT = Path(__file__).resolve().parent.parent
END_OF_TRACK = b"\xff\x2f\x00"
# Status nibbles carrying two data bytes, one data byte, and none. Meta and
# system-exclusive messages are framed by their own length and are handled apart.
TWO_DATA_BYTES = frozenset({0x80, 0x90, 0xA0, 0xB0, 0xE0})
ONE_DATA_BYTE = frozenset({0xC0, 0xD0})


def _read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    """Return one variable-length quantity and the offset past it."""
    value = 0
    while True:
        octet = data[offset]
        offset += 1
        value = (value << 7) | (octet & 0x7F)
        if not octet & 0x80:
            return value, offset


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """Return every (type, body) chunk, checking each declared length in turn.

    This is where the "declared length matches the real length" claim is made:
    a chunk whose header promises more bytes than the file holds runs off the
    end, and a file with bytes after its last chunk fails the final comparison.
    Neither can be seen by a reader that slices the one track it expects to find
    at the offset it expects to find it.
    """
    found: list[tuple[bytes, bytes]] = []
    offset = 0
    while offset < len(data):
        assert len(data) - offset >= 8, "a chunk header is truncated"
        kind = data[offset : offset + 4]
        length = int.from_bytes(data[offset + 4 : offset + 8], "big")
        offset += 8
        assert len(data) - offset >= length, f"{kind!r} declares more bytes than remain"
        found.append((kind, data[offset : offset + length]))
        offset += length
    assert offset == len(data), "trailing bytes follow the last chunk"
    return found


def _events(track: bytes) -> list[tuple[int, bytes]]:
    """Return (absolute tick, message) for a track, refusing anything unexpected.

    Running status is refused rather than decoded.  The renderer never emits it,
    so accepting it here would be accepting a shape the file is not supposed to
    contain, and the parser would then silently absorb a status byte lost to a
    bad delta time instead of reporting one.
    """
    events: list[tuple[int, bytes]] = []
    offset = 0
    tick = 0
    while offset < len(track):
        delta, offset = _read_vlq(track, offset)
        tick += delta
        status = track[offset]
        assert status & 0x80, "a message begins with a data byte (running status)"
        if status == 0xFF:
            length, payload = _read_vlq(track, offset + 2)
            end = payload + length
        else:
            assert status not in (0xF0, 0xF7), "no system-exclusive message is emitted"
            nibble = status & 0xF0
            assert nibble in TWO_DATA_BYTES | ONE_DATA_BYTE, "unknown status byte"
            end = offset + 1 + (2 if nibble in TWO_DATA_BYTES else 1)
        assert end <= len(track), "a message runs past the end of its track"
        events.append((tick, track[offset:end]))
        offset = end
    assert events, "the track carries no events at all"
    assert events[-1][1] == END_OF_TRACK, "the track does not end where it says"
    early = [message for _tick, message in events[:-1] if message == END_OF_TRACK]
    assert early == [], "an end-of-track event sits before the end of the track"
    return events


def _note_events(midi: bytes) -> list[tuple[int, bytes]]:
    """Return the note-on and note-off events of a single-track render, in order."""
    chunks = _chunks(midi)
    tracks = [body for kind, body in chunks if kind == b"MTrk"]
    assert len(tracks) == 1
    return [
        (tick, message)
        for tick, message in _events(tracks[0])
        if message[0] & 0xF0 in (0x80, 0x90)
    ]


def _item(graph: Graph, reference: ItemRef) -> Item:
    """Return the item a reference addresses."""
    tier = next(tier for tier in graph.tiers if tier.declaration.name == reference.tier)
    return tier.items[reference.index]


def _attribute(graph: Graph, reference: ItemRef, name: QualifiedName) -> int:
    """Return one integer attribute of an addressed item."""
    (value,) = [
        value for value in _item(graph, reference).attributes if value.name == name
    ]
    return int(value.lexical)


def _arrangement(graph: Graph, index: int) -> tuple[ItemRef, ...]:
    """Return the notes the arrangement path at `index` resolves to."""
    profile = MixPathProfile(graph)
    selected = resolve_path(
        graph, profile, f"/mix/arrangement/{index}", require=PathKind.ALTERNATIVE
    )
    assert isinstance(selected, ResolvedAlternative)
    return cast(tuple[ItemRef, ...], selected.value)


def _with_pitch(graph: Graph, reference: ItemRef, pitch: int) -> Graph:
    """Return the graph with one addressed note's pitch replaced."""
    tiers = list(graph.tiers)
    position = next(
        index
        for index, tier in enumerate(tiers)
        if tier.declaration.name == reference.tier
    )
    tier = tiers[position]
    items = list(tier.items)
    item = items[reference.index]
    items[reference.index] = replace(
        item,
        attributes=tuple(
            AttributeValue(value.name, value.value_type, str(pitch))
            if value.name == PITCH
            else value
            for value in item.attributes
        ),
    )
    tiers[position] = Tier(tier.declaration, tuple(items))
    return replace(graph, tiers=tuple(tiers))


def test_render_is_a_self_consistent_standard_midi_file() -> None:
    """The framing agrees with itself: chunk lengths, track count, and closure.

    Every number checked here is read out of the file and compared against
    another part of the same file, not against a constant transcribed from the
    renderer.  The header's track count is compared with the tracks actually
    present, each chunk's declared length with the bytes that follow it, and
    every sounding note with the note-off that ends it.  A render that dropped a
    track, mis-stated a length, or left a note hanging fails without anyone
    having to know in advance what the right answer was.

    None of this establishes that the file is audible or musical.  It
    establishes that a conforming reader can get to the end of it.
    """
    graph = build_graph()
    midi = render_midi(graph, MixPathProfile(graph))
    assert midi

    chunks = _chunks(midi)
    kind, header = chunks[0]
    assert kind == b"MThd"
    assert len(header) == 6
    smf_format = int.from_bytes(header[0:2], "big")
    declared_tracks = int.from_bytes(header[2:4], "big")
    division = int.from_bytes(header[4:6], "big")
    tracks = [body for kind, body in chunks[1:] if kind == b"MTrk"]
    assert len(chunks) == 1 + len(tracks), "a chunk of some other type is present"
    assert declared_tracks == len(tracks)
    # A format-0 file is one track by definition, so the count above is not free
    # to be anything the header felt like declaring.
    assert smf_format == 0
    assert declared_tracks == 1
    assert 1 <= division <= 0x7FFF

    events = _events(tracks[0])
    sounding: set[tuple[int, int]] = set()
    for _tick, message in events:
        status = message[0]
        if status & 0xF0 == 0x90:
            key = status & 0x0F, message[1]
            assert key not in sounding, "a note sounds twice before ending"
            sounding.add(key)
        elif status & 0xF0 == 0x80:
            sounding.discard((status & 0x0F, message[1]))
    assert sounding == set(), "the file ends with notes still sounding"


def test_note_events_are_reconstructed_from_the_addressed_arrangement() -> None:
    """Every note in the render comes from the items the path selected.

    This is the assertion the example exists to earn.  The expected events are
    not counted or copied: they are built from the graph items that
    `/mix/arrangement/{index}` resolves to, reading each note's channel, pitch,
    velocity, onset, and duration back out of the graph.  A renderer that
    ignored its input, emitted a fixed phrase, or read the wrong arrangement
    fails here, and so does one that renders the right notes at the wrong times.

    Both arrangements are checked, and their expected event sets are required to
    differ, so the path index has to be doing work rather than being accepted
    and discarded.
    """
    graph = build_graph()
    profile = MixPathProfile(graph)
    expectations: list[list[tuple[int, bytes]]] = []
    for index in (0, 1):
        references = _arrangement(graph, index)
        assert references
        expected: list[tuple[int, bytes]] = []
        for reference in references:
            channel = _attribute(graph, reference, CHANNEL)
            pitch = _attribute(graph, reference, PITCH)
            velocity = _attribute(graph, reference, VELOCITY)
            onset = _attribute(graph, reference, ONSET)
            duration = _attribute(graph, reference, DURATION)
            expected.append((onset, bytes((0x90 | channel, pitch, velocity))))
            expected.append((onset + duration, bytes((0x80 | channel, pitch, 0))))
        rendered = _note_events(render_midi(graph, profile, index))
        assert sorted(rendered) == sorted(expected)
        assert len(rendered) == 2 * len(references)
        expectations.append(sorted(expected))
    assert expectations[0] != expectations[1]
    assert render_midi(graph, profile, 0) != render_midi(graph, profile, 1)


def test_changing_one_addressed_note_changes_only_that_note_s_events() -> None:
    """A pitch edited through a path moves exactly the two events it addresses.

    The note is chosen by resolving a path and is named by spelling that path
    back, so what is perturbed is the thing the example's surface addresses
    rather than an item found by index.  The new pitch is derived from the old
    one, which is what makes this a discrimination rather than a golden: a
    renderer that ignored the graph produces no differences at all, and one that
    smeared the change across the arrangement produces too many.
    """
    graph = build_graph()
    profile = MixPathProfile(graph)
    reference = _arrangement(graph, 0)[7]
    assert str(profile.spell(ItemBinding(reference), graph)) == "/mix/midi/note/7"

    before_pitch = _attribute(graph, reference, PITCH)
    after_pitch = before_pitch + 1
    assert after_pitch <= 127
    changed = _with_pitch(graph, reference, after_pitch)

    before = _note_events(render_midi(graph, profile, 0))
    after = _note_events(render_midi(changed, MixPathProfile(changed), 0))
    differences = [
        (left, right)
        for left, right in zip(before, after, strict=True)
        if left != right
    ]
    assert len(differences) == 2
    assert [left[0] for left, _ in differences] == [
        right[0] for _, right in differences
    ]
    assert {left[1][1] for left, _ in differences} == {before_pitch}
    assert {right[1][1] for _, right in differences} == {after_pitch}
    assert {left[1][0] & 0xF0 for left, _ in differences} == {0x80, 0x90}


def test_render_is_byte_identical_across_calls_and_rebuilt_graphs() -> None:
    """The same arrangement renders to the same bytes, every time it is asked.

    Two renders from one graph and one from a separately built graph are
    required to agree, so a render whose event order came from set or dictionary
    iteration cannot pass.  The gate runs this suite under three interpreter
    hash seeds in separate processes, which is what turns that from a
    same-process coincidence into a claim about ordering.
    """
    first = build_graph()
    second = build_graph()
    for index in (0, 1):
        repeated = render_midi(first, MixPathProfile(first), index)
        assert repeated == render_midi(first, MixPathProfile(first), index)
        assert repeated == render_midi(second, MixPathProfile(second), index)


def test_default_output_is_one_file_whose_name_version_control_excludes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running the example bare writes exactly one file, and that name is ignored.

    The example is meant to be runnable as it stands, so it writes `mix.mid`
    into the working directory rather than demanding a path -- and for someone
    reading it in a checkout, that directory is the repository root.  Requiring
    an argument would trade the property the example is for tidiness in the
    tree, so the default stays and the ignore entry carries the cost of it.
    The build backend selects the working tree
    minus what version control ignores, so an unignored file left there is a file
    that ships; and the publishability gate reads the git index, where an
    untracked file does not appear.  The ignore entry is what closes that, and
    this test is what keeps the two spellings of the name together: renaming the
    default output without renaming the ignore entry fails here rather than in
    the next release.

    The whole directory is listed, not just the expected name, because the claim
    is that the example's footprint is one file and not merely that it includes
    one.
    """
    graph = build_graph()
    expected = render_midi(graph, MixPathProfile(graph))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["mix_paths"])
    assert main() == 0
    written = sorted(path.name for path in tmp_path.iterdir())
    assert written == ["mix.mid"]
    assert (tmp_path / "mix.mid").read_bytes() == expected

    patterns = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert written[0] in patterns
