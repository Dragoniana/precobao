"""Strict image decoding and bounded, metadata-free normalization."""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from hashlib import sha256

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_IMAGE_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class InvalidMedicineImage(ValueError):
    """Image payload is empty, unsafe, malformed, or cannot meet its size limit."""


@dataclass(frozen=True)
class NormalizedMedicineImage:
    content: bytes
    content_type: str
    sha256: str
    width: int
    height: int


def validate_normalized_medicine_image(
    content: bytes,
    *,
    declared_content_type: str,
    max_bytes: int = 512 * 1024,
    max_dimension: int = 1200,
) -> NormalizedMedicineImage:
    """Validate the collector's normalized output without a second lossy encode."""
    if not content:
        raise InvalidMedicineImage("empty_image")
    if len(content) > max_bytes:
        raise InvalidMedicineImage("image_too_large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(content))
            image_format = image.format
            actual_type = ALLOWED_FORMATS.get(image_format or "")
            if actual_type not in {"image/jpeg", "image/webp"}:
                raise InvalidMedicineImage("unsupported_image_format")
            if declared_content_type != actual_type:
                raise InvalidMedicineImage("image_mime_mismatch")
            if getattr(image, "n_frames", 1) != 1:
                raise InvalidMedicineImage("animated_image_rejected")
            if (
                image.width < 1
                or image.height < 1
                or image.width > max_dimension
                or image.height > max_dimension
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise InvalidMedicineImage("invalid_image_dimensions")
            metadata_keys = {
                "exif",
                "icc_profile",
                "xmp",
                "comment",
                "photoshop",
                "iptc",
            }
            if metadata_keys.intersection(key.casefold() for key in image.info):
                raise InvalidMedicineImage("image_metadata_not_stripped")
            if image.getexif():
                raise InvalidMedicineImage("image_metadata_not_stripped")
            image.verify()
            verified = Image.open(io.BytesIO(content))
            verified.load()
            width, height = verified.size
    except InvalidMedicineImage:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
    ) as error:
        raise InvalidMedicineImage("invalid_image_content") from error
    return NormalizedMedicineImage(
        content=content,
        content_type=actual_type,
        sha256=sha256(content).hexdigest(),
        width=width,
        height=height,
    )


def normalize_medicine_image(
    content: bytes,
    *,
    declared_content_type: str | None = None,
    max_bytes: int = 512 * 1024,
    max_dimension: int = 1200,
) -> NormalizedMedicineImage:
    if not content:
        raise InvalidMedicineImage("empty_image")
    if max_bytes < 1 or max_dimension < 1:
        raise ValueError("image limits must be positive")
    if len(content) > max(8 * 1024 * 1024, max_bytes * 16):
        raise InvalidMedicineImage("source_image_too_large")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(content))
            source_format = probe.format
            if (
                source_format not in ALLOWED_FORMATS
                or getattr(probe, "n_frames", 1) != 1
            ):
                raise InvalidMedicineImage("unsupported_image_format")
            if (
                probe.width < 1
                or probe.height < 1
                or probe.width * probe.height > MAX_IMAGE_PIXELS
            ):
                raise InvalidMedicineImage("invalid_image_dimensions")
            actual_type = ALLOWED_FORMATS[source_format]
            if (
                declared_content_type
                and declared_content_type.casefold() != actual_type
            ):
                raise InvalidMedicineImage("image_mime_mismatch")
            probe.verify()

            source = Image.open(io.BytesIO(content))
            source.load()
            source = ImageOps.exif_transpose(source)
            has_alpha = "A" in source.getbands() or "transparency" in source.info
            source = source.convert("RGBA" if has_alpha else "RGB")
    except InvalidMedicineImage:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
    ) as error:
        raise InvalidMedicineImage("invalid_image_content") from error

    sides = []
    current = max_dimension
    while current >= min(320, max_dimension):
        sides.append(current)
        if current == min(320, max_dimension):
            break
        current = max(min(320, max_dimension), int(current * 0.82))

    for side in sides:
        image = source.copy()
        image.thumbnail((side, side), Image.Resampling.LANCZOS)
        candidates = (("WEBP", "RGBA" if has_alpha else "RGB"),)
        if not has_alpha:
            candidates += (("JPEG", "RGB"),)
        else:
            candidates += (("JPEG", "RGB"),)
        for fmt, mode in candidates:
            if fmt == "JPEG" and has_alpha:
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                prepared = background
            else:
                prepared = image.convert(mode)
            for quality in (84, 80, 76, 72, 68):
                output = io.BytesIO()
                try:
                    if fmt == "WEBP":
                        prepared.save(output, format="WEBP", quality=quality, method=4)
                    else:
                        prepared.save(
                            output,
                            format="JPEG",
                            quality=quality,
                            optimize=True,
                            progressive=True,
                        )
                except (OSError, ValueError):
                    continue
                encoded = output.getvalue()
                if encoded and len(encoded) <= max_bytes:
                    media_type = "image/webp" if fmt == "WEBP" else "image/jpeg"
                    return NormalizedMedicineImage(
                        content=encoded,
                        content_type=media_type,
                        sha256=sha256(encoded).hexdigest(),
                        width=prepared.width,
                        height=prepared.height,
                    )
    raise InvalidMedicineImage("image_cannot_fit_configured_limit")
