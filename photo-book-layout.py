#!/usr/bin/env python3
"""Build a printable PDF photo book. Requires Python 3 and Pillow."""

import argparse
import io
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit(
        "photo-book-layout: Pillow is required; "
        "install it with 'python -m pip install Pillow'."
    )


# All layout dimensions are PDF points (72 per inch).
SIZES = {
    "letter": (612.0, 792.0),
    "a4": (210 * 72 / 25.4, 297 * 72 / 25.4),
}
MARGIN = 36
DPI = 300


def _lower(value):
    """Return a lowercased copy of the input string."""
    return value.lower()


def arguments():
    parser = argparse.ArgumentParser(
        prog="photo-book-layout",
        description="Arrange JPG and PNG files from one folder into a printable PDF.",
        epilog=(
            "Photos without a usable EXIF date are ordered by modification time. "
            "EXIF times without a time zone are interpreted as UTC."
        ),
        add_help=False,
    )
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="show this help message and exit",
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="folder containing the photos (non-recursive)",
    )
    parser.add_argument(
        "--size",
        type=_lower,
        choices=SIZES,
        default="letter",
        help="paper size (default: letter)",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        choices=(1, 2),
        default=1,
        help="number of photo slots per page (default: 1)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("photo-book.pdf"),
        help="output file (default: photo-book.pdf)",
    )
    parser.add_argument(
        "--title",
        help="book title (default: folder name)",
    )
    return parser, parser.parse_args()


def clean(value):
    """Normalize an EXIF value to a clean string."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            value = value.decode("latin-1")
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\0", " ").split())


def _exif_value(exif, tag):
    """Return an EXIF tag value, searching IFD0 and any loaded sub-IFDs."""
    if exif is None:
        return None
    try:
        return exif[tag]
    except KeyError:
        pass
    for ifd in getattr(exif, "_ifds", {}).values():
        try:
            return ifd[tag]
        except KeyError:
            pass
    return None


def exif_timestamp(exif):
    """Return a numeric UTC timestamp for an image's EXIF data, if any."""
    # (date tag, offset tag, sub-second tag)
    tag_groups = (
        (36867, 36881, 37521),  # DateTimeOriginal, OffsetTimeOriginal, SubsecTimeOriginal
        (36868, 36882, 37522),  # DateTimeDigitized, OffsetTimeDigitized, SubsecTimeDigitized
        (306, 36880, 37520),    # DateTime, OffsetTime, SubsecTime
    )
    for date_tag, offset_tag, subsec_tag in tag_groups:
        date_str = clean(_exif_value(exif, date_tag))
        if not date_str:
            continue
        try:
            dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue

        subsec = clean(_exif_value(exif, subsec_tag))
        if subsec and subsec.isdigit():
            dt = dt.replace(microsecond=int(subsec[:6].ljust(6, "0")))

        zone = clean(_exif_value(exif, offset_tag))
        if (
            len(zone) == 6
            and zone[0] in "+-"
            and zone[3] == ":"
        ):
            try:
                h, m = int(zone[1:3]), int(zone[4:])
            except ValueError:
                h, m = -1, -1
            if 0 <= h < 24 and 0 <= m < 60:
                delta = timedelta(hours=h, minutes=m)
                dt = dt.replace(
                    tzinfo=timezone(delta if zone[0] == "+" else -delta)
                )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        try:
            return dt.timestamp()
        except (ValueError, OverflowError, OSError):
            continue
    return None


def inspect_photo(path):
    """Extract ordering key, caption, and path for one photo."""
    with Image.open(path) as image:
        if image.format not in ("JPEG", "PNG"):
            raise ValueError("file is neither JPEG nor PNG")
        exif = image.getexif() or {}
        caption = clean(_exif_value(exif, 270)) or clean(path.stem) or path.name
        stamp = exif_timestamp(exif)
    return (path.stat().st_mtime if stamp is None else stamp, path.name, path, caption)


def jpeg_for_slot(path, width, height):
    """Render a photo to a JPEG-sized-to-fit a page slot. Returns (px_w, px_h, bytes)."""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)

        max_pixels = (
            math.ceil(width * DPI / 72),
            math.ceil(height * DPI / 72),
        )
        image.thumbnail(max_pixels, Image.Resampling.LANCZOS)

        if image.mode == "P" or "A" in image.getbands():
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        else:
            rgb = image.convert("RGB")

        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=94, subsampling=0)
        return rgb.width, rgb.height, buf.getvalue()


class PDF:
    """A minimal incremental PDF writer."""

    def __init__(self, file):
        self.file = file
        self.offsets = [0]
        file.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    def reserve(self):
        self.offsets.append(None)
        return len(self.offsets) - 1

    def obj(self, body, number=None, stream=None):
        if number is None:
            number = self.reserve()
        self.offsets[number] = self.file.tell()
        self.file.write(f"{number} 0 obj\n{body}".encode("ascii"))
        if stream is not None:
            self.file.write(b"\nstream\n")
            self.file.write(stream)
            self.file.write(b"\nendstream")
        self.file.write(b"\nendobj\n")
        return number

    def finish(self, root, info):
        start = self.file.tell()
        n = len(self.offsets)
        self.file.write(f"xref\n0 {n}\n0000000000 65535 f \n".encode("ascii"))
        for pos in self.offsets[1:]:
            if pos is None:
                raise ValueError("PDF object was reserved but never written")
            if pos > 9999999999:
                raise ValueError("output exceeds the PDF writer's 10 GB limit")
            self.file.write(f"{pos:010d} 00000 n \n".encode("ascii"))
        self.file.write(
            (
                f"trailer\n<< /Size {n} /Root {root} 0 R /Info {info} 0 R >>\n"
                f"startxref\n{start}\n%%EOF\n"
            ).encode("ascii")
        )


def text_line(text, x, y, size, width):
    """Draw one line of text using the standard Courier font."""
    text = clean(text)
    # Courier advance is 0.6 * font size.
    limit = max(1, int(width / (size * 0.6)))
    if len(text) > limit:
        if limit > 3:
            text = text[: limit - 3] + "..."
        else:
            text = text[:limit]
    data = text.encode("cp1252", "replace")
    return f"BT /F {size} Tf {x:.3f} {y:.3f} Td <{data.hex()}> Tj ET\n"


def write_book(file, photos, size, per_page, title):
    if per_page not in (1, 2):
        raise ValueError("per_page must be 1 or 2")

    pdf = PDF(file)
    pages = pdf.reserve()
    font = pdf.obj(
        "<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
    )

    page_ids = []
    w, h = SIZES[size]
    width = w - 2 * MARGIN
    top = h - MARGIN - 30
    bottom = MARGIN + 20
    slot = (top - bottom - 16 * (per_page - 1)) / per_page

    for start in range(0, len(photos), per_page):
        commands = text_line(title, MARGIN, h - MARGIN - 12, 15, width)
        commands += text_line(str(len(page_ids) + 1), MARGIN, MARGIN, 9, width)

        resources = []
        for index, (_, _, path, caption) in enumerate(photos[start : start + per_page]):
            base = top - index * (slot + 16) - slot
            available = slot - 22

            try:
                iw, ih, data = jpeg_for_slot(path, width, available)
            except Exception as exc:
                raise ValueError(f"cannot render {path}: {exc}") from exc

            ref = pdf.obj(
                f"<< /Type /XObject /Subtype /Image /Width {iw} /Height {ih} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                f"/Length {len(data)} >>",
                stream=data,
            )
            resources.append(f"/Im{index} {ref} 0 R")

            scale = min(width / iw, available / ih)
            dw, dh = iw * scale, ih * scale
            x = MARGIN + (width - dw) / 2
            y = base + 22 + (available - dh) / 2
            commands += f"q {dw:.3f} 0 0 {dh:.3f} {x:.3f} {y:.3f} cm /Im{index} Do Q\n"
            commands += text_line(caption, MARGIN, base + 5, 10, width)

        content = commands.encode("ascii")
        content_ref = pdf.obj(f"<< /Length {len(content)} >>", stream=content)
        page_ids.append(
            pdf.obj(
                f"<< /Type /Page /Parent {pages} 0 R "
                f"/MediaBox [0 0 {w:.4f} {h:.4f}] "
                f"/Resources << /Font << /F {font} 0 R >> "
                f"/XObject << {' '.join(resources)} >> >> "
                f"/Contents {content_ref} 0 R >>"
            )
        )

    kids = " ".join(f"{p} 0 R" for p in page_ids)
    pdf.obj(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>",
        pages,
    )
    root = pdf.obj(f"<< /Type /Catalog /Pages {pages} 0 R >>")
    encoded_title = title.encode("utf-16-be", "replace").hex()
    info = pdf.obj(
        f"<< /Title <feff{encoded_title}> /Creator (photo-book-layout) >>"
    )
    pdf.finish(root, info)


def main():
    parser, args = arguments()
    tmp = None
    try:
        if not args.folder.is_dir():
            raise ValueError(f"not a folder: {args.folder}")

        photos = []
        for path in args.folder.iterdir():
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            if not path.is_file():
                continue
            if args.out.exists() and args.out.samefile(path):
                raise ValueError(f"output would overwrite an input photo: {path}")
            try:
                photos.append(inspect_photo(path))
            except Exception as exc:
                raise ValueError(f"cannot read {path}: {exc}") from exc

        if not photos:
            raise ValueError(f"no JPG or PNG photos found in {args.folder}")

        photos.sort(key=lambda p: (p[0], p[1].casefold(), p[1]))
        title = (
            args.title
            if args.title is not None
            else args.folder.resolve().name or "Photo Book"
        )

        args.out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=args.out.parent,
            prefix=".photo-book-",
            delete=False,
        ) as file:
            tmp = Path(file.name)
            write_book(file, photos, args.size, args.per_page, title)
        os.replace(tmp, args.out)
        tmp = None

    except (OSError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
