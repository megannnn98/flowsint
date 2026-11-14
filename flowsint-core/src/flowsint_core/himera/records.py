"""
Deterministic merge of records returned for overlapping page ranges.

Record identity is positional and stable: ``p<page_start>-<ordinal>`` where the
ordinal is the record's position among records starting on that page. Two copies
of one record (from neighbouring, overlapping ranges) share that key; identical
looking records at different positions do not, so real duplicates survive.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .pdf import PageRange
from .schemas import FieldValue, LlmRecordsOutput, SourceRecord

CONTINUATION_HEADING = "<continuation>"


@dataclass
class ChunkRecords:
    page_range: PageRange
    output: LlmRecordsOutput


@dataclass
class MergeResult:
    records: List[SourceRecord]
    report_id: Optional[str]
    truncation_notice: bool
    warnings: List[str] = field(default_factory=list)


def _norm_heading(heading: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", heading.casefold())


def _record_id(page_start: int, ordinal: int, suffix: str = "") -> str:
    return f"p{page_start:02d}-{ordinal:02d}{suffix}"


def _abs_page(rel: int, page_range: PageRange, warnings: List[str], what: str) -> int:
    if rel < 1 or rel > page_range.pages:
        warnings.append(
            f"{what}: page {rel} outside range {page_range} (relative), clamped"
        )
        rel = min(max(rel, 1), page_range.pages)
    return page_range.first + rel - 1


def _words(heading: str) -> set:
    return set(re.findall(r"[0-9a-zа-яё]+", heading.casefold()))


def similar_headings(a: str, b: str) -> bool:
    """Same heading read differently (wrapped lines, a dropped word): one
    contains the other, or most words are shared."""
    na, nb = _norm_heading(a), _norm_heading(b)
    if not na or not nb:
        return False
    if min(len(na), len(nb)) >= 6 and (na in nb or nb in na):
        return True
    wa, wb = _words(a), _words(b)
    # Periods/codes must agree: "ЖИТЕЛИ РОССИИ 2010 03" is not "… 2010 07".
    if {w for w in wa if w.isdigit()} != {w for w in wb if w.isdigit()}:
        return False
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6


def _add_fields(target: SourceRecord, extra: List[FieldValue]) -> None:
    seen = {(f.name, f.value) for f in target.fields}
    for f in extra:
        if (f.name, f.value) not in seen:
            target.fields.append(f)
            seen.add((f.name, f.value))


def merge_chunk_records(chunks: List[ChunkRecords], page_count: int) -> MergeResult:
    merged: Dict[Tuple[int, str, int], SourceRecord] = {}
    # Reading order of the first sighting, by object id of the kept copy.
    order: Dict[int, Tuple[int, int]] = {}
    warnings: List[str] = []
    report_id: Optional[str] = None
    truncation = False

    for chunk_no, chunk in enumerate(chunks):
        rng = chunk.page_range
        label = str(rng)
        report_id = report_id or chunk.output.report_id
        truncation = truncation or chunk.output.truncation_notice
        # Occurrence of a heading among records starting on a page, counted by
        # us from the answer order: models miscount ordinals on overlap pages.
        occurrences: Dict[Tuple[int, str], int] = {}
        matched: set = set()  # merged keys already matched by this range
        for idx, rec in enumerate(chunk.output.records):
            what = f"range {label} record {idx + 1}"
            start = _abs_page(rec.page_start, rng, warnings, what)
            end = max(start, _abs_page(rec.page_end, rng, warnings, what))
            open_ended = rec.continues_after_range and end < page_count

            is_tail = (
                rec.starts_before_range or rec.source_heading == CONTINUATION_HEADING
            )
            if is_tail and idx > 0:
                # Only the very start of the range can be the tail of an earlier
                # record; seen on the real report: later records wrongly flagged.
                warnings.append(
                    f"{what}: marked as continuation but is not the first record; "
                    "kept as a separate record"
                )
                is_tail = False
            if is_tail:
                # The tail belongs to a record that started before this range
                # (records starting on the first page are seen whole here).
                target = next(
                    (
                        r
                        for r in sorted(
                            merged.values(),
                            key=lambda r: (r.page_start, order[id(r)]),
                            reverse=True,
                        )
                        if r.page_start < rng.first
                        and (r.open_ended or r.page_end >= rng.first)
                    ),
                    None,
                )
                if target is not None:
                    _add_fields(target, rec.fields)
                    target.page_end = max(target.page_end, end)
                    target.open_ended = open_ended
                    if rec.raw_text and rec.raw_text not in target.raw_text:
                        target.raw_text = f"{target.raw_text}\n{rec.raw_text}".strip()
                    if label not in target.chunks:
                        target.chunks.append(label)
                    continue
                if rng.first > 1:
                    warnings.append(
                        f"{what}: continuation without a known start on page {start}"
                    )

            if rec.source_heading == CONTINUATION_HEADING:
                rec = rec.model_copy(update={"source_heading": "(без заголовка)"})
            heading = _norm_heading(rec.source_heading)
            occurrence = occurrences.get((start, heading), 0) + 1
            occurrences[(start, heading)] = occurrence
            key = (start, heading, occurrence)
            candidate = SourceRecord(
                record_id="",
                section=rec.section,
                source_name=rec.source_name,
                source_heading=rec.source_heading,
                source_period=rec.source_period,
                page_start=start,
                page_end=end,
                ordinal_on_start_page=rec.ordinal_on_start_page,
                fields=list(rec.fields),
                raw_text=rec.raw_text,
                name_match_only=rec.name_match_only,
                open_ended=open_ended,
                chunks=[label],
            )
            existing = merged.get(key)
            if existing is None:
                # Same record from an overlapping range whose heading was read
                # slightly differently.
                fuzzy = next(
                    (
                        k
                        for k, r in merged.items()
                        if k[0] == start
                        and k not in matched
                        and label not in r.chunks
                        and similar_headings(r.source_heading, rec.source_heading)
                    ),
                    None,
                )
                if fuzzy is not None:
                    key, existing = fuzzy, merged[fuzzy]
            if existing is not None:
                matched.add(key)
            if existing is None:
                merged[key] = candidate
                matched.add(key)
                order[id(candidate)] = (chunk_no, idx)
                continue
            better = (existing.open_ended and not candidate.open_ended) or (
                existing.open_ended == candidate.open_ended
                and len(candidate.fields) > len(existing.fields)
            )
            if better:
                candidate.chunks = existing.chunks + [label]
                order[id(candidate)] = order[id(existing)]
                merged[key] = candidate
            else:
                existing.chunks.append(label)

    # Stable ids: position among records starting on the page, in reading order
    # (first sighting).
    records = sorted(merged.values(), key=lambda r: (r.page_start, order[id(r)]))
    per_page: Dict[int, int] = {}
    for r in records:
        per_page[r.page_start] = per_page.get(r.page_start, 0) + 1
        r.ordinal_on_start_page = per_page[r.page_start]
        r.record_id = _record_id(r.page_start, r.ordinal_on_start_page)
    for r in records:
        if r.open_ended:
            warnings.append(
                f"record {r.record_id} may continue beyond page {r.page_end}"
            )
    return MergeResult(records, report_id, truncation, warnings)
