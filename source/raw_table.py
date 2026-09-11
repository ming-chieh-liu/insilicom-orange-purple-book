"""Read an FDA delimited table into rows, from a plain file or inside a zip.

Four properties of the corpus shape this reader, all measured 2026-09-11:

**Line endings differ between files, and one code path handles both.** Every
Orange Book and Drugs@FDA ``.txt`` is CRLF; ``UNII_Names`` is LF-only. Opening
with ``newline=None`` — Python's universal-newline mode — makes the two
identical, so there is no per-file flag and no ``\\r`` left glued to the last
column. Without it, ``Applicant_Full_Name`` and ``SponsorName`` silently gain a
carriage return, which then travels into every join.

**Encoding differs between files too, and not the way you would guess.**
``Submissions.txt`` holds 18 non-ASCII bytes (15 ``–``, 3 ``’``) and
``ApplicationDocs.txt`` 1 (``\\xa0``); both are cp1252 and **fail** a strict
UTF-8 decode. ``UNII_Names`` is the exact reverse — 130,797 non-ASCII bytes of
valid UTF-8 that cp1252 would mangle. So the rule is measured, not configured:
try UTF-8, fall back to cp1252 with one warning naming the file.

**The last row may have no newline.** ``orangebook/x/products.txt`` ends mid-row;
its 48,664th record is real and ``wc -l`` undercounts by one.

**A ragged row is a defect, not a re-alignment.** Every file's rows have exactly
the declared field count — verified across all 17 files, zero exceptions — which
means the delimiter never appears inside a value and a row with the wrong count
is corrupt. It is skipped with a warning naming its line number, never padded
or truncated, because a mis-aligned row puts one column's value under another
column's name.

Error policy, as everywhere in this package: a defect in the data is skipped
and counted; a missing file is silent, because a gitignored rebuildable tree is
legitimately absent.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

from config.settings import DelimitedFile
from utility.logging import get_logger

_log = get_logger(__name__)

#: Tried in order. UTF-8 first because a file that is valid UTF-8 must not be
#: read as cp1252; cp1252 second because it decodes any byte, so it cannot
#: fail and must never be tried first.
_ENCODINGS: tuple[str, ...] = ("utf-8", "cp1252")


def _decode(data: bytes, name: str) -> str:
    """Text from bytes, trying UTF-8 then cp1252."""
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # cp1252 maps every byte, so this is unreachable in practice; raising
    # rather than returning mojibake keeps the contract honest.
    raise UnicodeDecodeError(  # pragma: no cover
        "cp1252", data, 0, 1, f"{name}: no encoding in {_ENCODINGS} decoded this file"
    )


def _read_text(spec: DelimitedFile) -> str | None:
    """The whole file as text, or ``None`` if it is not there."""
    if spec.member is None:
        if not spec.path.is_file():
            return None
        return _decode(spec.path.read_bytes(), str(spec.path))
    if not spec.path.is_file():
        return None
    with zipfile.ZipFile(spec.path) as archive:
        try:
            data = archive.read(spec.member)
        except KeyError:
            _log.warning("%s: no member %r", spec.path, spec.member)
            return None
    return _decode(data, f"{spec.path}!{spec.member}")


def read_rows(spec: DelimitedFile) -> Iterator[dict[str, str]]:
    """Yield each data row as ``column -> value``.

    The header is the first line. Values are not stripped: trailing spaces are
    real data here — ``'19019 '`` is a genuine ``ApplNo`` — and stripping them
    would hide the very defect the normalisers exist to handle.
    """
    text = _read_text(spec)
    if text is None:
        return

    # Universal newlines: CRLF and LF both become '\n', so no '\r' survives on
    # the last column, and the final row is kept whether or not it ends in one.
    lines = io.StringIO(text, newline=None).read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return

    header = lines[0].split(spec.delimiter)
    if len(header) != spec.fields:
        _log.warning(
            "%s: header has %d fields, expected %d; not read",
            spec.member or spec.path, len(header), spec.fields,
        )
        return

    for number, line in enumerate(lines[1:], start=2):
        values = line.split(spec.delimiter)
        if len(values) != spec.fields:
            _log.warning(
                "%s: line %d has %d fields, expected %d; skipped",
                spec.member or spec.path, number, len(values), spec.fields,
            )
            continue
        yield dict(zip(header, values))


def count_rows(spec: DelimitedFile) -> int:
    """How many data rows the file yields. For verifying a download.

    Counts what :func:`read_rows` would actually hand a caller, so a skipped
    ragged row is excluded — the number to compare against
    :attr:`~config.settings.DelimitedFile.rows`.
    """
    return sum(1 for _ in read_rows(spec))


__all__ = ["count_rows", "read_rows"]
