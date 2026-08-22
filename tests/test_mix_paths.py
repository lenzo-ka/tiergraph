"""The shipped mix profile exercises every generic TG-PATH reference kind."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from examples.mix_paths import (
    ARRANGEMENT,
    BUS,
    CHANNEL,
    CLOCK,
    DIVISION,
    DURATION,
    MIDI_NAMESPACE,
    NOTE,
    ONSET,
    PITCH,
    STEM,
    TEMPO,
    TITLE,
    VELOCITY,
    MixPathProfile,
    _vlq,
    build_graph,
    main,
    render_midi,
    run_example,
)

from tiergraph import (
    AlternativeRef,
    AttributeValue,
    CanonicalPath,
    DurableItemRef,
    Graph,
    ItemBinding,
    ItemRef,
    PathKind,
    PathRefusal,
    PathRefusalCode,
    PositionBinding,
    PositionRef,
    QualifiedName,
    ResolvedAlternative,
    ResolvedItem,
    ResolvedPosition,
    Tier,
    XsdType,
    resolve_path,
)


def refusal(profile: MixPathProfile, text: str) -> PathRefusal:
    """Resolve text and return its expected typed refusal."""
    with pytest.raises(PathRefusal) as caught:
        resolve_path(profile.graph, profile, text)
    return caught.value


def test_resolves_and_round_trips_all_three_path_kinds() -> None:
    """Items, shared-clock positions, and both diamond routes stay distinct."""
    graph = build_graph()
    profile = MixPathProfile(graph)
    item = resolve_path(graph, profile, "/mix/midi/note/0", require=PathKind.ITEM)
    bus = resolve_path(graph, profile, "/mix/mix/bus/0")
    position = resolve_path(graph, profile, "/mix/clock/2", require=PathKind.POSITION)
    first = resolve_path(
        graph, profile, "/mix/arrangement/0", require=PathKind.ALTERNATIVE
    )
    second = resolve_path(graph, profile, "/mix/arrangement/1")
    assert isinstance(item, ResolvedItem)
    assert isinstance(bus, ResolvedItem)
    assert isinstance(position, ResolvedPosition)
    assert isinstance(first, ResolvedAlternative)
    assert isinstance(second, ResolvedAlternative)
    assert item.current == ItemRef(NOTE, 0)
    assert bus.current == ItemRef(BUS, 0)
    assert position.current == PositionRef(CLOCK, 2)
    assert first.value == tuple(ItemRef(NOTE, index) for index in range(26))
    assert second.value == tuple(ItemRef(NOTE, index) for index in range(26, 52))
    bindings = (
        ItemBinding(item.current),
        PositionBinding(position.current),
        AlternativeRef(first.owner, first.relation, first.index),
    )
    paths = (item.path, position.path, first.path)
    assert tuple(profile.spell(binding, graph) for binding in bindings) == paths
    assert tuple(profile.bind(path, graph) for path in paths) == bindings


def test_arrangement_bounds_kind_and_path_lexicals_are_typed() -> None:
    """The resolver owns bounds/kind checks while the profile owns its lexicon."""
    profile = MixPathProfile(build_graph())
    out = refusal(profile, "/mix/arrangement/2")
    assert out.code is PathRefusalCode.ALTERNATIVE_OUT_OF_RANGE
    assert out.offender.available_count == 2
    with pytest.raises(PathRefusal) as wrong:
        resolve_path(
            profile.graph,
            profile,
            "/mix/arrangement/0",
            require=PathKind.ITEM,
        )
    assert wrong.value.code is PathRefusalCode.WRONG_KIND
    assert refusal(profile, "/other").code is PathRefusalCode.UNKNOWN_FORM
    assert (
        refusal(profile, "/mix/no-such-ring/stem/0").code
        is PathRefusalCode.UNKNOWN_FORM
    )
    assert (
        refusal(profile, "/mix/mix/stem/01").code
        is PathRefusalCode.NONCANONICAL_SEGMENT
    )
    assert (
        refusal(profile, "/mix/clock/+2").code is PathRefusalCode.NONCANONICAL_SEGMENT
    )
    assert (
        refusal(profile, "/mix/arrangement/nope").code
        is PathRefusalCode.INVALID_SEGMENT
    )


def test_snapshot_and_owner_relation_provenance_are_guarded() -> None:
    """A profile cannot drift to another graph, owner, or relation."""
    graph = build_graph()
    profile = MixPathProfile(graph)
    other = replace(graph)
    path = CanonicalPath.parse("/mix/mix/stem/0")
    with pytest.raises(PathRefusal) as bind_snapshot:
        profile.bind(path, other)
    assert bind_snapshot.value.offender.profile_reason == "different_mix_snapshot"
    with pytest.raises(PathRefusal) as spell_snapshot:
        profile.spell(ItemBinding(ItemRef(STEM, 0)), other)
    assert spell_snapshot.value.offender.profile_reason == "different_mix_snapshot"
    with pytest.raises(PathRefusal) as alternatives_snapshot:
        profile.alternatives(ItemRef(BUS, 0), ARRANGEMENT, other)
    assert (
        alternatives_snapshot.value.offender.profile_reason == "different_mix_snapshot"
    )

    wrong_relation = QualifiedName(ARRANGEMENT.namespace, "other")
    with pytest.raises(PathRefusal) as relation:
        profile.alternatives(ItemRef(BUS, 0), wrong_relation, graph)
    assert relation.value.offender.profile_reason == "unsupported_relation"
    with pytest.raises(PathRefusal) as owner:
        profile.alternatives(ItemRef(BUS, 1), ARRANGEMENT, graph)
    assert owner.value.offender.profile_reason == "unsupported_owner"


def test_spell_refuses_bindings_outside_the_mix_vocabulary() -> None:
    """Spelling checks reference shape, tier, owner, and relation provenance."""
    graph = build_graph()
    profile = MixPathProfile(graph)
    bad_bindings = (
        ItemBinding(DurableItemRef("bed")),
        ItemBinding(ItemRef(CLOCK, 0)),
        ItemBinding(ItemRef(QualifiedName(MIDI_NAMESPACE, "other"), 0)),
        PositionBinding(PositionRef(STEM, 0)),
        AlternativeRef(ItemRef(BUS, 1), ARRANGEMENT, 0),
        AlternativeRef(
            ItemRef(BUS, 0), QualifiedName(ARRANGEMENT.namespace, "other"), 0
        ),
    )
    for binding in bad_bindings:
        with pytest.raises(PathRefusal) as caught:
            profile.spell(binding, graph)
        assert caught.value.code is PathRefusalCode.UNSPELLABLE


def _read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        octet = data[offset]
        offset += 1
        value = (value << 7) | (octet & 0x7F)
        if octet & 0x80 == 0:
            return value, offset


def _parse_midi(
    data: bytes,
) -> tuple[int, str, list[tuple[int, int, int, int]]]:
    assert data[:8] == b"MThd\x00\x00\x00\x06"
    assert data[8:12] == b"\x00\x00\x00\x01"
    division = int.from_bytes(data[12:14], "big")
    assert data[14:18] == b"MTrk"
    length = int.from_bytes(data[18:22], "big")
    track = data[22:]
    assert length == len(track)
    offset = 0
    delta, offset = _read_vlq(track, offset)
    assert delta == 0
    assert track[offset : offset + 2] == b"\xff\x03"
    offset += 2
    title_length, offset = _read_vlq(track, offset)
    title = track[offset : offset + title_length].decode("ascii")
    offset += title_length
    delta, offset = _read_vlq(track, offset)
    assert delta == 0
    assert track[offset : offset + 6] == b"\xff\x51\x03\x07\xa1\x20"
    offset += 6
    tick = 0
    events: list[tuple[int, int, int, int]] = []
    while track[offset : offset + 4] != b"\x00\xff\x2f\x00":
        delta, offset = _read_vlq(track, offset)
        tick += delta
        status, first, second = track[offset : offset + 3]
        offset += 3
        assert status & 0xF0 in (0x80, 0x90, 0xB0)
        assert 0 <= second <= 127
        events.append((tick, status, first, second))
    assert offset + 4 == len(track)
    return division, title, events


def _note_intervals(
    events: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int, int]]:
    active: dict[tuple[int, int], tuple[int, int]] = {}
    intervals: list[tuple[int, int, int, int, int]] = []
    for tick, status, pitch, velocity in events:
        kind = status & 0xF0
        if kind not in (0x80, 0x90):
            continue
        channel = status & 0x0F
        key = channel, pitch
        if kind == 0x90:
            active[key] = tick, velocity
        else:
            onset, attack = active.pop(key)
            intervals.append((onset, tick, channel, pitch, attack))
    assert active == {}
    return intervals


def test_rendered_midi_is_valid_paired_format_zero_and_alternates() -> None:
    """Both voicings produce structurally valid, distinct playable SMFs."""
    graph = build_graph()
    profile = MixPathProfile(graph)
    straight = render_midi(graph, profile, 0)
    turn = render_midi(graph, profile, 1)
    assert straight.startswith(b"MThd")
    assert straight != turn
    division, title, events = _parse_midi(straight)
    assert division == 480
    assert title == TITLE == "Tropic of Capricorn"
    note_events = [event for event in events if event[1] & 0xF0 in (0x80, 0x90)]
    assert len(note_events) == 52
    channels = {status & 0x0F for _tick, status, _pitch, _value in note_events}
    assert channels == {0, 1}
    intervals = _note_intervals(events)
    assert len(intervals) == 26
    channel_zero = [interval for interval in intervals if interval[2] == 0]
    channel_one = [interval for interval in intervals if interval[2] == 1]
    assert any(
        max(left[0], right[0]) < min(left[1], right[1])
        for left in channel_zero
        for right in channel_one
    )

    grids = {
        channel: sorted(
            tick
            for tick, status, _pitch, _velocity in note_events
            if status == 0x90 | channel and tick < 7680
        )
        for channel in channels
    }
    gaps = {
        channel: [right - left for left, right in zip(ticks, ticks[1:], strict=False)]
        for channel, ticks in grids.items()
    }
    assert min(gaps[0]) * 3 == min(gaps[1]) * 4 == 1920
    assert set(gaps[0]) == {640, 1280}
    assert set(gaps[1]) == {480, 960}

    for voice in (channel_zero, channel_one):
        ordered = sorted(voice)
        for interval, following in zip(ordered[:-1], ordered[1:], strict=True):
            inter_onset = following[0] - interval[0]
            assert interval[1] - interval[0] >= inter_onset
    velocities = {interval[4] for interval in intervals}
    assert min(velocities) >= 45
    assert max(velocities) <= 100
    assert len(velocities) > 8

    controls = [event for event in events if event[1] & 0xF0 == 0xB0]
    for channel, extreme in ((0, 0), (1, 127)):
        pans = [
            (tick, value)
            for tick, status, controller, value in controls
            if status == 0xB0 | channel and controller == 10
        ]
        assert pans[0] == (0, extreme)
        assert pans[-1] == (7680, 64)
        assert all(
            abs(64 - left[1]) >= abs(64 - right[1])
            for left, right in zip(pans, pans[1:], strict=False)
        )
        volumes = [
            value
            for _tick, status, controller, value in controls
            if status == 0xB0 | channel and controller == 7
        ]
        assert volumes == [48, 60, 74, 90, 108, 116, 108, 98, 86]

    lead_pans: list[int] = []
    for index, (tick, status, _pitch, _velocity) in enumerate(events):
        if status != 0x90:
            continue
        pan_tick, pan_status, controller, pan = events[index - 1]
        assert (pan_tick, pan_status, controller) == (tick, 0xB0, 10)
        lead_pans.append(pan)
    assert lead_pans[:2] == [0, 127]
    assert all(
        (left < 64 and right > 64) or (left > 64 and right < 64)
        for left, right in zip(lead_pans[:-2], lead_pans[1:-1], strict=True)
    )
    assert all(
        abs(value - 64) >= abs(following - 64)
        for value, following in zip(lead_pans, lead_pans[1:], strict=False)
    )
    assert lead_pans[-1] == 64

    turn_events = _parse_midi(turn)[2]
    assert intervals[-1] == _note_intervals(turn_events)[-1]
    assert intervals[-1] == (7680, 8640, 1, 60, 66)


def _with_value(graph: Graph, name: QualifiedName, lexical: str) -> Graph:
    return replace(
        graph,
        attributes=tuple(
            AttributeValue(value.name, value.value_type, lexical)
            if value.name == name
            else value
            for value in graph.attributes
        ),
    )


def _with_note_value(graph: Graph, name: QualifiedName, lexical: str) -> Graph:
    tiers = list(graph.tiers)
    note_index = next(
        index for index, tier in enumerate(tiers) if tier.declaration.name == NOTE
    )
    note_tier = tiers[note_index]
    items = list(note_tier.items)
    item = items[0]
    items[0] = replace(
        item,
        attributes=tuple(
            AttributeValue(value.name, XsdType.INTEGER, lexical)
            if value.name == name
            else value
            for value in item.attributes
        ),
    )
    tiers[note_index] = Tier(note_tier.declaration, tuple(items))
    return replace(graph, tiers=tuple(tiers))


@pytest.mark.parametrize(
    ("value", "encoded"),
    [(0, b"\x00"), (127, b"\x7f"), (128, b"\x81\x00"), (16383, b"\xff\x7f")],
)
def test_vlq_boundaries(value: int, encoded: bytes) -> None:
    """MIDI VLQs cover single- and multiple-octet delta-time boundaries."""
    assert _vlq(value) == encoded
    with pytest.raises(ValueError, match="negative"):
        _vlq(-1)
    with pytest.raises(ValueError, match="four bytes"):
        _vlq(0x10000000)


def test_renderer_rejects_invalid_midi_domains() -> None:
    """SMF numeric limits are checked even though graph integers are broader."""
    graph = build_graph()
    cases = (
        _with_value(graph, DIVISION, "0"),
        _with_value(graph, TEMPO, "16777216"),
        _with_note_value(graph, PITCH, "128"),
        _with_note_value(graph, VELOCITY, "0"),
        _with_note_value(graph, CHANNEL, "16"),
        _with_note_value(graph, ONSET, "-1"),
        _with_note_value(graph, DURATION, "0"),
    )
    for candidate in cases:
        with pytest.raises(ValueError):
            render_midi(candidate, MixPathProfile(candidate))


def test_rendering_tracks_a_selected_graph_note_without_spurious_changes() -> None:
    """Changing one graph pitch changes exactly that note's on/off pair."""
    graph = build_graph()
    changed = _with_note_value(graph, PITCH, "49")
    original_events = _parse_midi(render_midi(graph, MixPathProfile(graph)))[2]
    changed_events = _parse_midi(render_midi(changed, MixPathProfile(changed)))[2]
    differences = [
        (before, after)
        for before, after in zip(original_events, changed_events, strict=True)
        if before != after
    ]
    assert differences == [
        ((0, 0x90, 60, 62), (0, 0x90, 49, 62)),
        ((680, 0x80, 60, 0), (680, 0x80, 49, 0)),
    ]


def test_run_example_and_main_write_stable_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API returns MIDI and the CLI writes only its requested destination."""
    result = run_example()
    assert result["item"] == ItemRef(NOTE, 0).to_data()
    midi = result["midi"]
    assert isinstance(midi, bytes)
    requested = tmp_path / "phrase.mid"
    assert main([str(requested)]) == 0
    assert requested.read_bytes() == midi
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["mix_paths"])
    assert main() == 0
    assert (tmp_path / "mix.mid").read_bytes() == midi
