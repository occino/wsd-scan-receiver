import pytest

from wsd_scan_receiver.epson import make_epson_command_frame, make_epson_read_frame


def test_make_epson_command_frame() -> None:
    frame = make_epson_command_frame("INFO")

    assert frame.startswith(bytes.fromhex("49532000000c00000014"))
    assert frame.endswith(b"INFOx0000000")
    assert len(frame) == 32


def test_make_epson_read_frame() -> None:
    assert make_epson_read_frame(0x78) == bytes.fromhex(
        "49532000000c0000000800000000000000000078",
    )


def test_make_epson_command_frame_rejects_invalid_command() -> None:
    with pytest.raises(ValueError):
        make_epson_command_frame("STATUS")
