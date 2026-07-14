#!/usr/bin/env python3
"""Losslessly recompress PNG files.

In the default PIL mode, image data is re-saved with optimize/compress_level=9 while
preserving InvokeAI-readable metadata keys. In raw-zlib mode, all non-IDAT chunks are
copied verbatim and only the IDAT stream is recompressed.
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, PngImagePlugin


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHUNK_HEADER_STRUCT = struct.Struct(">I4s")
CHUNK_LENGTH_LIMIT = 1024 * 1024
IDAT = b"IDAT"
IEND = b"IEND"
APNG_CONTROL_CHUNKS = {b"acTL", b"fcTL", b"fdAT"}


@dataclass(frozen=True)
class PngChunk:
    """Parsed PNG chunk."""

    chunk_type: bytes
    data: bytes
    crc: int


@dataclass(frozen=True)
class ProcessResult:
    """Result of processing one file."""

    status: str
    original_size: int
    new_size: int | None = None
    detail: str | None = None


class UnsupportedPngError(ValueError):
    """Raised when a PNG cannot be safely handled by the chosen engine."""


def parse_args() -> argparse.Namespace:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Recursively recompress PNG files with zlib level 9 while preserving all "
            "non-IDAT chunks exactly as they are."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("path", type=Path, help="PNG file or directory to process")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Do not recurse into subdirectories when the input path is a directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without rewriting any files",
    )
    parser.add_argument(
        "--rewrite-if-different",
        action="store_true",
        help=(
            "Replace the original whenever recompressed bytes differ, even if the new file "
            "is not smaller"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print a line for every processed PNG",
    )
    parser.add_argument(
        "--engine",
        choices=("pil", "raw-zlib"),
        default="pil",
        help=(
            "Compression engine: 'pil' usually gives smaller files by recalculating PNG "
            "filters, while 'raw-zlib' preserves all non-IDAT chunks byte-for-byte"
        ),
    )
    parser.add_argument(
        "--zip-text-metadata",
        action="store_true",
        help=(
            "Store PNG text metadata as compressed zTXt chunks in PIL mode. Disabled by "
            "default to match InvokeAI's usual tEXt output more closely"
        ),
    )
    return parser.parse_args()


def iter_png_files(path: Path, recursive: bool) -> Iterable[Path]:
    """Yield PNG files from a file or directory input."""
    if path.is_file():
        if path.suffix.lower() == ".png":
            yield path
        return

    if not path.is_dir():
        return

    iterator = path.rglob("*") if recursive else path.glob("*")
    for candidate in iterator:
        if candidate.is_file() and candidate.suffix.lower() == ".png":
            yield candidate


def read_chunks(payload: bytes) -> list[PngChunk]:
    """Parse all PNG chunks and validate their CRCs."""
    if not payload.startswith(PNG_SIGNATURE):
        raise ValueError("missing PNG signature")

    offset = len(PNG_SIGNATURE)
    chunks: list[PngChunk] = []

    while offset < len(payload):
        if offset + 12 > len(payload):
            raise ValueError("truncated chunk header")

        length, chunk_type = CHUNK_HEADER_STRUCT.unpack_from(payload, offset)
        offset += CHUNK_HEADER_STRUCT.size

        data_end = offset + length
        crc_end = data_end + 4
        if crc_end > len(payload):
            raise ValueError(f"truncated chunk payload for {chunk_type.decode('ascii', 'replace')}")

        data = payload[offset:data_end]
        crc = struct.unpack(">I", payload[data_end:crc_end])[0]
        expected_crc = zlib.crc32(chunk_type)
        expected_crc = zlib.crc32(data, expected_crc) & 0xFFFFFFFF
        if crc != expected_crc:
            raise ValueError(f"CRC mismatch in {chunk_type.decode('ascii', 'replace')}")

        chunks.append(PngChunk(chunk_type=chunk_type, data=data, crc=crc))
        offset = crc_end

        if chunk_type == IEND:
            if offset != len(payload):
                raise ValueError("unexpected trailing data after IEND")
            return chunks

    raise ValueError("missing IEND chunk")


def build_png(chunks: list[PngChunk], compressed_idat: bytes) -> bytes:
    """Build a new PNG stream using original non-IDAT chunks and new IDAT payload."""
    output = bytearray(PNG_SIGNATURE)
    wrote_idat = False

    for chunk in chunks:
        if chunk.chunk_type == IDAT:
            if wrote_idat:
                continue
            for piece in split_bytes(compressed_idat, CHUNK_LENGTH_LIMIT):
                output.extend(pack_chunk(IDAT, piece))
            wrote_idat = True
            continue

        output.extend(pack_chunk(chunk.chunk_type, chunk.data, crc=chunk.crc))

    if not wrote_idat:
        raise ValueError("missing IDAT chunk")

    return bytes(output)


def split_bytes(data: bytes, piece_size: int) -> Iterable[bytes]:
    """Yield fixed-size slices from a bytes object."""
    for offset in range(0, len(data), piece_size):
        yield data[offset : offset + piece_size]


def pack_chunk(chunk_type: bytes, data: bytes, crc: int | None = None) -> bytes:
    """Serialize a PNG chunk."""
    if crc is None:
        crc = zlib.crc32(chunk_type)
        crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", crc)
    )


def recompress_png_raw_zlib(payload: bytes) -> bytes:
    """Recompress concatenated IDAT data at zlib level 9."""
    chunks = read_chunks(payload)
    if any(chunk.chunk_type in APNG_CONTROL_CHUNKS for chunk in chunks):
        raise UnsupportedPngError("APNG is not supported by raw-zlib mode and was skipped")

    idat_stream = b"".join(chunk.data for chunk in chunks if chunk.chunk_type == IDAT)
    if not idat_stream:
        raise UnsupportedPngError("missing IDAT chunk")

    filtered_scanlines = zlib.decompress(idat_stream)
    recompressed = zlib.compress(filtered_scanlines, level=9)
    return build_png(chunks, recompressed)


def build_pnginfo(image: Image.Image, zip_text_metadata: bool) -> PngImagePlugin.PngInfo | None:
    """Copy textual PNG metadata into a PngInfo container."""
    text_chunks = getattr(image, "text", {})
    if not text_chunks:
        return None

    pnginfo = PngImagePlugin.PngInfo()
    for key, value in text_chunks.items():
        if isinstance(value, str):
            pnginfo.add_text(key, value, zip=zip_text_metadata)
    return pnginfo


def recompress_png_pil(path: Path, zip_text_metadata: bool) -> bytes:
    """Re-save a PNG through Pillow with optimize/compress_level=9."""
    with Image.open(path) as image:
        if getattr(image, "is_animated", False):
            raise UnsupportedPngError("APNG is not supported by PIL mode and was skipped")

        save_kwargs: dict[str, object] = {
            "format": "PNG",
            "optimize": True,
            "compress_level": 9,
        }

        pnginfo = build_pnginfo(image, zip_text_metadata=zip_text_metadata)
        if pnginfo is not None:
            save_kwargs["pnginfo"] = pnginfo

        for key in ("exif", "icc_profile", "dpi", "transparency", "gamma"):
            value = image.info.get(key)
            if value is not None:
                save_kwargs[key] = value

        encoderinfo = getattr(image, "encoderinfo", {})
        if "bits" in encoderinfo:
            save_kwargs["bits"] = encoderinfo["bits"]

        output = io.BytesIO()
        image.save(output, **save_kwargs)
        return output.getvalue()


def recompress_png(path: Path, original_bytes: bytes, engine: str, zip_text_metadata: bool) -> bytes:
    """Dispatch to the selected recompression engine."""
    if engine == "pil":
        return recompress_png_pil(path, zip_text_metadata=zip_text_metadata)
    if engine == "raw-zlib":
        return recompress_png_raw_zlib(original_bytes)
    raise ValueError(f"Unsupported engine: {engine}")


def rewrite_file(path: Path, data: bytes, original_stat: os.stat_result) -> None:
    """Atomically replace the file and preserve its timestamps and mode."""
    with tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
        dir=path.parent,
        prefix=f"{path.stem}.",
        suffix=".tmp",
    ) as temp_file:
        temp_file.write(data)
        temp_name = Path(temp_file.name)

    try:
        os.chmod(temp_name, original_stat.st_mode)
        os.utime(temp_name, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        os.replace(temp_name, path)
    finally:
        if temp_name.exists():
            temp_name.unlink(missing_ok=True)


def process_file(
    path: Path,
    dry_run: bool,
    rewrite_if_different: bool,
    engine: str,
    zip_text_metadata: bool,
) -> ProcessResult:
    """Process a single PNG file."""
    original_stat = path.stat()
    original_bytes = path.read_bytes()
    original_size = len(original_bytes)

    new_bytes = recompress_png(
        path,
        original_bytes,
        engine=engine,
        zip_text_metadata=zip_text_metadata,
    )
    new_size = len(new_bytes)

    if new_bytes == original_bytes:
        return ProcessResult("unchanged", original_size=original_size, new_size=new_size)

    if not rewrite_if_different and new_size >= original_size:
        return ProcessResult(
            "kept",
            original_size=original_size,
            new_size=new_size,
            detail="recompressed file was not smaller",
        )

    if not dry_run:
        rewrite_file(path, new_bytes, original_stat)

    status = "would-rewrite" if dry_run else "rewritten"
    return ProcessResult(status, original_size=original_size, new_size=new_size)


def print_result(path: Path, result: ProcessResult) -> None:
    """Render one result line."""
    relative_path = path.as_posix()
    if result.new_size is None:
        if result.detail:
            print(f"[{result.status}] {relative_path} | {result.detail}")
        else:
            print(f"[{result.status}] {relative_path}")
        return

    delta = result.original_size - result.new_size
    if delta > 0:
        summary = f"{result.original_size} -> {result.new_size} bytes (-{delta})"
    elif delta < 0:
        summary = f"{result.original_size} -> {result.new_size} bytes (+{-delta})"
    else:
        summary = f"{result.original_size} -> {result.new_size} bytes"

    if result.detail:
        print(f"[{result.status}] {relative_path} | {summary} | {result.detail}")
    else:
        print(f"[{result.status}] {relative_path} | {summary}")


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    target_path = args.path.expanduser().resolve()
    recursive = not args.no_recursive

    if not target_path.exists():
        print(f"Path not found: {target_path}", file=sys.stderr)
        return 1

    png_files = list(iter_png_files(target_path, recursive=recursive))
    if not png_files:
        print("No PNG files found.")
        return 1

    stats = {
        "rewritten": 0,
        "would-rewrite": 0,
        "unchanged": 0,
        "kept": 0,
        "skipped": 0,
        "errors": 0,
        "saved_bytes": 0,
    }

    for png_path in png_files:
        try:
            result = process_file(
                png_path,
                dry_run=args.dry_run,
                rewrite_if_different=args.rewrite_if_different,
                engine=args.engine,
                zip_text_metadata=args.zip_text_metadata,
            )
        except UnsupportedPngError as error:
            result = ProcessResult("skipped", original_size=png_path.stat().st_size, detail=str(error))
        except ValueError as error:
            result = ProcessResult("errors", original_size=png_path.stat().st_size, detail=str(error))
        except zlib.error as error:
            result = ProcessResult("errors", original_size=png_path.stat().st_size, detail=str(error))
        except OSError as error:
            result = ProcessResult("errors", original_size=png_path.stat().st_size, detail=str(error))

        stats[result.status] = stats.get(result.status, 0) + 1
        if result.status in {"rewritten", "would-rewrite"} and result.new_size is not None:
            stats["saved_bytes"] += max(result.original_size - result.new_size, 0)

        if args.verbose or result.status not in {"unchanged"}:
            print_result(png_path, result)

    print(
        "\nSummary: "
        f"total={len(png_files)}, "
        f"rewritten={stats['rewritten']}, "
        f"would_rewrite={stats['would-rewrite']}, "
        f"unchanged={stats['unchanged']}, "
        f"kept={stats['kept']}, "
        f"skipped={stats['skipped']}, "
        f"errors={stats['errors']}, "
        f"saved_bytes={stats['saved_bytes']}"
    )

    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
