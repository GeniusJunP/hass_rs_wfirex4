"""Tests for the RS-WFIREX4 wire format."""

import pytest
from infrared_protocols.commands.fujitsu_ac import FujitsuAcCommand, FujitsuAcMode
from infrared_protocols.commands.panasonic_ac import PanasonicAcCommand, PanasonicAcMode
from rs_wfirex4_protocol import (
    Wfirex4ProtocolError,
    build_frame,
    build_send_frame,
    parse_learn_response,
    parse_send_response,
    timings_to_wave,
    wave_to_timings,
)

# Learn response captured from an RS-WFIREXU while a Fujitsu General remote resent
# its state: heat, 24 C, fan auto, power bit clear. The remote is an ARRY4-family one,
# which also sets the filter bit.
LEARNED_FUJITSU_HEAT_24 = bytes.fromhex(
    "aa01081200000103201104050404040c0404040c040404050405040d040d040504050405040d"
    "040d0405040504050405040504050405040504050405040504050405040d0405040504050405"
    "040504050405040d0405040504050405040d040d040d040d040d040d040c040d04050405040d"
    "04050405040504050405040504050405040d040d040504050405040504050405040504050405"
    "040d04050405040d040504050405040504050405040504050405040504050405040504050405"
    "0405040504050405040504050405040504050405040504050405040504050405040504050405"
    "040504050405040504050405040d0405040d0405040504050405040d04050405040d04050405"
    "04c9"
)

FUJITSU_HEAT_24 = FujitsuAcCommand(temperature=24, mode=FujitsuAcMode.HEAT, filter=True)


def test_request_frames_match_the_known_constants():
    assert build_frame(b"\x18") == bytes.fromhex("aa00011850")
    assert build_frame(b"\x12") == bytes.fromhex("aa0001126c")


def test_send_frame_carries_the_waveform_length():
    assert build_send_frame(bytes([32, 17, 4])) == build_frame(
        bytes.fromhex("11000003201104")
    )


def test_send_response_success_is_accepted():
    parse_send_response(bytes.fromhex("aa00021100d1"))


def test_send_response_with_bad_crc_is_rejected():
    with pytest.raises(Wfirex4ProtocolError):
        parse_send_response(bytes.fromhex("aa00021100d2"))


def test_learn_response_yields_the_waveform_without_the_crc():
    wave = parse_learn_response(LEARNED_FUJITSU_HEAT_24)

    assert len(wave) == 259
    assert wave[:2] == bytes([0x20, 0x11])
    assert wave[-1] == 0x04


def test_failed_learning_is_rejected():
    with pytest.raises(Wfirex4ProtocolError, match="status 07"):
        parse_learn_response(bytes.fromhex("aa000512070000005b"))


def test_learned_fujitsu_state_decodes_to_the_same_command():
    timings = wave_to_timings(parse_learn_response(LEARNED_FUJITSU_HEAT_24))

    decoded = FujitsuAcCommand.from_raw_timings(timings)

    assert decoded is not None
    assert decoded.data == FUJITSU_HEAT_24.data


def test_fujitsu_command_matches_the_learned_waveform_within_one_unit():
    learned = parse_learn_response(LEARNED_FUJITSU_HEAT_24)

    wave = timings_to_wave(FUJITSU_HEAT_24.get_raw_timings())

    assert len(wave) == len(learned)
    assert all(abs(a - b) <= 1 for a, b in zip(wave, learned, strict=True))


@pytest.mark.parametrize(
    "command",
    [
        FUJITSU_HEAT_24,
        FujitsuAcCommand(temperature=25, mode=FujitsuAcMode.HEAT, power=True),
    ],
)
def test_fujitsu_commands_survive_the_conversion(command):
    timings = wave_to_timings(timings_to_wave(command.get_raw_timings()))

    decoded = type(command).from_raw_timings(timings)

    assert decoded is not None
    assert decoded.data == command.data


def test_panasonic_command_keeps_its_two_frames_and_gap():
    command = PanasonicAcCommand(temperature=24, mode=PanasonicAcMode.HEAT)
    timings = command.get_raw_timings()

    converted = wave_to_timings(timings_to_wave(timings))

    expected = [round(t / 100) * 100 for t in timings]
    while expected[-1] < 0:
        expected.pop()
    assert converted == expected


@pytest.mark.parametrize(
    ("timings", "wave"),
    [
        # Intervals are rounded half up to 0.1 ms, with a minimum of one unit.
        ([450, -449, 30], [5, 4, 1]),
        # Values of one sign are merged; zero-length values are ignored.
        ([400, 0, 400, -400, -400, 400], [8, 8, 4]),
        # Leading and trailing off time is dropped.
        ([-1000, 400, -400, 400, -8000], [4, 4, 4]),
        # 0xFF is 25.5 ms that continues into the next byte.
        ([400, -50000, 400], [4, 0xFF, 245, 4]),
        ([400, -51000, 400], [4, 0xFF, 0xFF, 0, 4]),
        ([400, -25500, 400], [4, 0xFF, 0, 4]),
        ([400, -25400, 400], [4, 254, 4]),
        ([30000, -400, 400], [0xFF, 45, 4, 4]),
    ],
)
def test_timings_to_wave(timings, wave):
    assert timings_to_wave(timings) == bytes(wave)


@pytest.mark.parametrize(
    ("wave", "timings"),
    [
        ([4, 4, 4], [400, -400, 400]),
        # A long gap as the device learns it.
        ([4, 0xFF, 0xFF, 0xFF, 220, 31], [400, -98500, 3100]),
        # A zero-length interval joins its neighbours.
        ([4, 254, 0, 246, 4], [400, -50000, 400]),
    ],
)
def test_wave_to_timings(wave, timings):
    assert wave_to_timings(bytes(wave)) == timings
