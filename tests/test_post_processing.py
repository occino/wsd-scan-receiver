from io import BytesIO
from pathlib import Path

from PIL import Image

from wsd_scan_receiver.config import PostProcessingSettings
from wsd_scan_receiver.post_processing import store_scan_payload


def _document_on_dark_background() -> bytes:
    image = Image.new("RGB", (10, 10), "black")
    for x in range(0, 6):
        for y in range(0, 8):
            image.putpixel((x, y), (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _content_on_white_background() -> bytes:
    image = Image.new("RGB", (10, 10), "white")
    for x in range(3, 7):
        for y in range(2, 8):
            image.putpixel((x, y), (0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _document_on_white_scanner_bed() -> bytes:
    image = Image.new("RGB", (100, 140), (246, 246, 246))
    for x in range(0, 70):
        for y in range(0, 92):
            image.putpixel((x, y), (238, 238, 238))
    for y in range(0, 92):
        image.putpixel((69, y), (170, 170, 170))
    for x in range(0, 70):
        image.putpixel((x, 91), (170, 170, 170))
    for x in range(8, 62):
        for y in range(12, 15):
            image.putpixel((x, y), (60, 60, 60))
    for x in range(8, 55):
        for y in range(40, 43):
            image.putpixel((x, y), (60, 60, 60))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _wide_scan_bed() -> bytes:
    image = Image.new("RGB", (120, 140), (245, 245, 245))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_store_scan_payload_crops_document_from_dark_background_when_enabled(
    tmp_path: Path,
) -> None:
    out_path = store_scan_payload(
        tmp_path / "out",
        "scan",
        ".png",
        _document_on_dark_background(),
        post_processing_settings=PostProcessingSettings(enabled=True),
        temp_root=tmp_path / "temp",
    )

    with Image.open(out_path) as image:
        assert image.size == (6, 8)


def test_store_scan_payload_does_not_crop_content_on_white_background(
    tmp_path: Path,
) -> None:
    out_path = store_scan_payload(
        tmp_path / "out",
        "scan",
        ".png",
        _content_on_white_background(),
        post_processing_settings=PostProcessingSettings(enabled=True),
        temp_root=tmp_path / "temp",
    )

    with Image.open(out_path) as image:
        assert image.size == (10, 10)


def test_store_scan_payload_crops_document_from_white_scanner_bed(
    tmp_path: Path,
) -> None:
    out_path = store_scan_payload(
        tmp_path / "out",
        "scan",
        ".png",
        _document_on_white_scanner_bed(),
        post_processing_settings=PostProcessingSettings(enabled=True),
        temp_root=tmp_path / "temp",
    )

    with Image.open(out_path) as image:
        assert image.size == (70, 92)


def test_store_scan_payload_uses_fixed_din_a4_crop_mode(tmp_path: Path) -> None:
    out_path = store_scan_payload(
        tmp_path / "out",
        "scan",
        ".png",
        _wide_scan_bed(),
        post_processing_settings=PostProcessingSettings(enabled=True, crop_mode="DIN-A4"),
        temp_root=tmp_path / "temp",
    )

    with Image.open(out_path) as image:
        assert image.size == (99, 140)


def test_store_scan_payload_keeps_image_when_disabled(tmp_path: Path) -> None:
    out_path = store_scan_payload(
        tmp_path / "out",
        "scan",
        ".png",
        _document_on_dark_background(),
        post_processing_settings=PostProcessingSettings(enabled=False),
        temp_root=tmp_path / "temp",
    )

    with Image.open(out_path) as image:
        assert image.size == (10, 10)


def test_store_scan_payload_keeps_image_when_crop_mode_none(tmp_path: Path) -> None:
    out_path = store_scan_payload(
        tmp_path / "out",
        "scan",
        ".png",
        _document_on_dark_background(),
        post_processing_settings=PostProcessingSettings(crop_mode="none"),
        temp_root=tmp_path / "temp",
    )

    with Image.open(out_path) as image:
        assert image.size == (10, 10)


def test_store_scan_payload_uses_separate_side_and_bottom_padding(tmp_path: Path) -> None:
    out_path = store_scan_payload(
        tmp_path / "out",
        "scan",
        ".png",
        _document_on_dark_background(),
        post_processing_settings=PostProcessingSettings(
            enabled=True,
            crop_side_padding=1,
            crop_bottom_padding=2,
        ),
        temp_root=tmp_path / "temp",
    )

    with Image.open(out_path) as image:
        assert image.size == (7, 10)
