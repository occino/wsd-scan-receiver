"""Post-processing pipeline for received scan payloads."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from .config import PostProcessingSettings

LOGGER = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
TEMP_ROOT = Path("/tmp/wsd-scan-receiver")
FOREGROUND_PROJECTION_RATIO = 0.005
DIN_A4_ASPECT_RATIO = 210 / 297


def _luminance(pixel: tuple[int, int, int]) -> int:
    return int((pixel[0] * 0.299) + (pixel[1] * 0.587) + (pixel[2] * 0.114))


def _corner_background_luminance(image: Image.Image) -> int:
    width, height = image.size
    corners = [
        image.getpixel((0, 0)),
        image.getpixel((width - 1, 0)),
        image.getpixel((0, height - 1)),
        image.getpixel((width - 1, height - 1)),
    ]
    return sum(_luminance(pixel) for pixel in corners) // len(corners)


def _padded_bbox(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    side_padding: int,
    bottom_padding: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bbox
    return (
        left,
        top,
        min(width, right + side_padding),
        min(height, bottom + bottom_padding),
    )


def _meets_min_document_size(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    settings: PostProcessingSettings,
) -> bool:
    crop_width = bbox[2] - bbox[0]
    crop_height = bbox[3] - bbox[1]
    return (
        crop_width >= width * (settings.min_document_width_percent / 100)
        and crop_height >= height * (settings.min_document_height_percent / 100)
    )


def _crop_in_place(
    image: Image.Image,
    path: Path,
    bbox: tuple[int, int, int, int],
    settings: PostProcessingSettings,
) -> bool:
    if bbox == (0, 0, image.width, image.height):
        return False
    if not _meets_min_document_size(
        bbox,
        width=image.width,
        height=image.height,
        settings=settings,
    ):
        return False
    padded = _padded_bbox(
        bbox,
        width=image.width,
        height=image.height,
        side_padding=settings.crop_side_padding,
        bottom_padding=settings.crop_bottom_padding,
    )
    cropped = image.crop(padded)
    cropped.save(path)
    return True


def _din_a4_bbox(width: int, height: int) -> tuple[int, int, int, int]:
    if width / height > DIN_A4_ASPECT_RATIO:
        crop_width = max(1, round(height * DIN_A4_ASPECT_RATIO))
        return (0, 0, min(width, crop_width), height)
    crop_height = max(1, round(width / DIN_A4_ASPECT_RATIO))
    return (0, 0, width, min(height, crop_height))


def _light_document_bbox(
    comparison: Image.Image,
    *,
    background: int,
    settings: PostProcessingSettings,
) -> tuple[int, int, int, int] | None:
    threshold = min(255, background + settings.document_contrast)
    mask = comparison.convert("L").point(lambda pixel: 255 if pixel > threshold else 0)
    return mask.getbbox()


def _foreground_projection_bbox(
    comparison: Image.Image,
    *,
    background: int,
    settings: PostProcessingSettings,
) -> tuple[int, int, int, int] | None:
    grayscale = comparison.convert("L")
    threshold = max(0, background - max(10, settings.document_contrast))
    pixels = grayscale.load()
    width, height = grayscale.size

    foreground_columns: list[int] = []
    min_column_pixels = max(1, int(height * FOREGROUND_PROJECTION_RATIO))
    for x in range(width):
        count = 0
        for y in range(height):
            if pixels[x, y] < threshold:
                count += 1
        if count >= min_column_pixels:
            foreground_columns.append(x)

    foreground_rows: list[int] = []
    min_row_pixels = max(1, int(width * FOREGROUND_PROJECTION_RATIO))
    for y in range(height):
        count = 0
        for x in range(width):
            if pixels[x, y] < threshold:
                count += 1
        if count >= min_row_pixels:
            foreground_rows.append(y)

    if not foreground_columns or not foreground_rows:
        return None

    detected_bbox = (
        min(foreground_columns),
        min(foreground_rows),
        max(foreground_columns) + 1,
        max(foreground_rows) + 1,
    )
    if not _meets_min_document_size(
        detected_bbox,
        width=width,
        height=height,
        settings=settings,
    ):
        return None

    _left, _top, right, bottom = detected_bbox
    return (0, 0, right, bottom)


def _crop_document_image(path: Path, settings: PostProcessingSettings) -> bool:
    """Crop a document from the scan background in place."""
    with Image.open(path) as image:
        if settings.crop_mode == "none":
            return False

        if settings.crop_mode == "DIN-A4":
            bbox = _din_a4_bbox(image.width, image.height)
            if bbox == (0, 0, image.width, image.height):
                return False
            cropped = image.crop(bbox)
            cropped.save(path)
            return True

        comparison = image.convert("RGB")
        background = _corner_background_luminance(comparison)

        if background < settings.background_threshold:
            bbox = _light_document_bbox(
                comparison,
                background=background,
                settings=settings,
            )
            if bbox is not None:
                bbox = (0, 0, bbox[2], bbox[3])
            if bbox is not None and _crop_in_place(image, path, bbox, settings):
                return True

        bbox = _foreground_projection_bbox(
            comparison,
            background=background,
            settings=settings,
        )
        if bbox is None:
            return False
        return _crop_in_place(image, path, bbox, settings)


def post_process_scan_file(
    path: Path,
    suffix: str,
    settings: PostProcessingSettings,
) -> bool:
    """Apply supported post-processing steps to a temporary scan file."""
    if suffix.lower() not in IMAGE_SUFFIXES:
        return False
    try:
        return _crop_document_image(path, settings)
    except OSError:
        LOGGER.warning("scan image post-processing failed", exc_info=True)
        return False


def store_scan_payload(
    output_dir: Path,
    prefix: str,
    suffix: str,
    payload: bytes,
    *,
    post_processing_settings: PostProcessingSettings | None = None,
    post_processing_enabled: bool | None = None,
    temp_root: Path = TEMP_ROOT,
) -> Path:
    """Store a scan payload, optionally processing it in a temp directory first."""
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / _timestamped_name(prefix, suffix)
    settings = post_processing_settings or PostProcessingSettings(
        enabled=True if post_processing_enabled is None else post_processing_enabled
    )
    if not settings.enabled or settings.crop_mode == "none":
        return _write_final(final_path, payload)

    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as temp_directory:
        temp_path = Path(temp_directory) / f"scan{suffix}"
        temp_path.write_bytes(payload)
        cropped = post_process_scan_file(temp_path, suffix, settings)
        with tempfile.NamedTemporaryFile(
            dir=output_dir,
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            with temp_path.open("rb") as source:
                shutil.copyfileobj(source, handle)
            output_tmp = Path(handle.name)
        output_tmp.replace(final_path)
        LOGGER.info(
            "stored post-processed scan payload",
            extra={"path": str(final_path), "cropped": cropped},
        )
        return final_path


def _write_final(path: Path, payload: bytes) -> Path:
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        tmp_path = Path(handle.name)
    tmp_path.replace(path)
    return path


def _timestamped_name(prefix: str, suffix: str) -> str:
    from .receiver import timestamped_name

    return timestamped_name(prefix, suffix)
