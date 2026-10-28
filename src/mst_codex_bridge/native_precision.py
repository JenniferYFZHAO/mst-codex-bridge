"""Read-only, evidence-constrained extraction of MST float32 nodal results.

This is a LOG-constrained binary search, not a general GRD format parser.
The source GRD and LOG are opened for reading only. Ambiguity is failure.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

# Adapted core only; I/O is handled by the workspace-bounded service.
SECTION = re.compile(r'^/([^/]+)/$')
U_HEADER = re.compile(r'^//\(U\)/')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Case:
    key: str
    section: str
    node_ids: list[int]
    tokens: list[str]
    line_start: int
    line_end: int


def decimal_interval(token: str):
    """Open decimal rounding cell; exact half-step ties are not accepted.

    Signed printed zero additionally constrains the IEEE sign bit. We do not
    assume a platform-specific half-to-even / half-away tie implementation.
    """
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise ValueError(f'Invalid LOG number {token!r}') from exc
    if not value.is_finite():
        raise ValueError(f'Non-finite LOG number {token!r}')
    step = Decimal(1).scaleb(value.as_tuple().exponent)
    half = step / 2
    lower, upper, numeric_step = float(value-half), float(value+half), float(step)
    if not all(np.isfinite(x) for x in (lower,upper,numeric_step)) or lower >= upper or numeric_step <= 0:
        raise ValueError(f'LOG precision cannot be represented safely: {token!r}')
    return lower, upper, value == 0, value.is_signed(), numeric_step


def decode_log(raw: bytes):
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError('LOG is neither UTF-8 nor GB18030; refusing lossy decoding')


def parse_cases(text: str):
    lines = text.splitlines()
    section = ''
    geometry_ids = []
    cases = []
    excluded = []
    occurrences = {}
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        match = SECTION.match(t)
        if match:
            section = match.group(1).upper()
        elif section in ('NODECORD', 'NODECORD2') and t and not t.startswith(('#', '/')):
            w = t.split()
            if len(w) >= 4 and w[0].isdigit():
                geometry_ids.append(int(w[0]))
        elif U_HEADER.match(t) and section.startswith('RES'):
            start = i + 1
            ids, tokens = [], []
            j = i + 1
            while j < len(lines):
                row = lines[j].strip()
                if row.startswith(('#', '/')):
                    break
                if row:
                    w = row.split()
                    if len(w) != 7 or not w[0].isdigit():
                        raise ValueError(f'U row at LOG line {j+1} must contain node ID and exactly six components')
                    ids.append(int(w[0]))
                    for token in w[1:]:
                        decimal_interval(token)
                    tokens.extend(w[1:])
                j += 1
            if not ids:
                raise ValueError(f'Empty U block at LOG line {start}')
            if len(ids) != len(set(ids)) or min(ids) <= 0:
                raise ValueError(f'Duplicate or invalid node IDs in {section} at line {start}')
            if section == 'RESLOADCASE':
                excluded.append({'section': section, 'line_start': start, 'reason': 'combined_case_not_independent_result'})
            else:
                occurrences[section] = occurrences.get(section, 0) + 1
                key = section if occurrences[section] == 1 else f'{section}__U{occurrences[section]:02d}'
                cases.append(Case(key, section, ids, tokens, start, j))
            i = j - 1
        i += 1
    if not geometry_ids or len(geometry_ids) != len(set(geometry_ids)):
        raise ValueError('A unique NODECORD/NODECORD2 node list is required')
    for case in cases:
        if case.node_ids != geometry_ids:
            raise ValueError(f'{case.key}: U node order or coverage differs from LOG geometry')
    if not cases:
        raise ValueError('No independent six-component U blocks found')
    return cases, excluded


def match_case(data: bytes, case: Case, max_candidates: int = 50000):
    count = len(case.tokens)
    intervals = [decimal_interval(t) for t in case.tokens]
    lo, hi = np.array([r[0] for r in intervals]), np.array([r[1] for r in intervals])
    zeros = np.array([r[2] for r in intervals], dtype=bool)
    negative = np.array([r[3] for r in intervals], dtype=bool)
    steps = np.array([r[4] for r in intervals])
    report = {'case': case.key, 'section': case.section, 'log_U_header_line_1based': case.line_start,
              'log_U_last_line_1based': case.line_end, 'node_count': len(case.node_ids),
              'component_count': count, 'dtype': 'little_endian_float32', 'scanned_byte_phases': [0,1,2,3],
              'rounding_rule': 'open_half_decimal_step_interval; signed_zero_bit_must_match; midpoint_ties_excluded',
              'nonzero_printed_components': int((~zeros).sum()), 'status': 'FAILED'}
    if zeros.all():
        report.update(reason='UNINFORMATIVE_ALL_ROUNDED_ZERO_CASE',
                      detail='Zero printed components cannot identify a unique native result block; no scan-based zero inference attempted.')
        return report, None
    if len(data) < count*4:
        report.update(reason='GRD_TOO_SHORT_FOR_CONTIGUOUS_ARRAY')
        return report, None

    def matches(values, indices):
        # Arbitrary binary offsets can contain signalling NaNs. They remain
        # invalid candidates; avoid emitting a warning during intentional scan.
        with np.errstate(invalid='ignore'):
            values = np.asarray(values, dtype=np.float64)
        valid = np.isfinite(values) & (values > lo[indices]) & (values < hi[indices])
        valid &= ~zeros[indices] | (np.signbit(values) == negative[indices])
        return valid

    # Rare, nonzero components are economical anchors. Every surviving complete
    # array is still checked against every LOG component; this is not a score.
    printed = np.array([float(Decimal(t)) for t in case.tokens])
    score = np.abs(printed) / steps
    anchor_pool = np.argsort(-score, kind='stable')
    anchors = []
    min_separation = max(1, count//96)
    for idx in anchor_pool:
        idx = int(idx)
        if score[idx] == 0:
            break
        if all(abs(idx-prev) >= min_separation for prev in anchors):
            anchors.append(idx)
        if len(anchors) == 32:
            break
    report['anchor_component_indices_0based'] = anchors
    prefilter = []
    candidates = []
    for phase in range(4):
        size = (len(data)-phase)//4
        if size < count:
            continue
        arr = np.frombuffer(data, dtype='<f4', count=size, offset=phase)
        windows = size-count+1
        first = anchors[0]
        hits = np.flatnonzero(matches(arr[first:first+windows], first))
        for idx in anchors[1:]:
            hits = hits[matches(arr[hits+idx], idx)]
            if not len(hits):
                break
        prefilter.append({'byte_phase': phase, 'remaining_candidates': len(hits)})
        if len(hits) > max_candidates:
            report.update(reason='CANDIDATE_LIMIT_REACHED_NO_GUESS', prefilter=prefilter,
                          max_candidates=max_candidates)
            return report, None
        for pos in hits:
            pos = int(pos)
            if matches(arr[pos:pos+count], np.arange(count)).all():
                candidates.append(phase+4*pos)
    report['prefilter'] = prefilter
    report['full_interval_match_offsets_bytes_0based'] = candidates
    report['full_interval_match_count'] = len(candidates)
    if not candidates:
        report['reason'] = 'NO_FULL_ARRAY_MATCH'
        return report, None
    groups = {}
    for offset in candidates:
        payload = data[offset:offset+count*4]
        groups.setdefault(payload, []).append(offset)
    report['distinct_matched_payload_count'] = len(groups)
    report['matched_groups'] = [{'offsets_bytes_0based': offsets, 'payload_sha256': sha(payload),
                                 'payload_bytes': len(payload)} for payload, offsets in groups.items()]
    if len(groups) != 1:
        report['reason'] = 'MULTIPLE_DIFFERENT_MATCHING_ARRAYS_NO_GUESS'
        return report, None
    payload, offsets = next(iter(groups.items()))
    values = np.frombuffer(payload, dtype='<f4').astype(np.float64).reshape(-1,6)
    report.update(status='PASS', reason='UNIQUE_MATCH' if len(offsets)==1 else 'MULTIPLE_BYTE_IDENTICAL_MATCHES',
                  payload_sha256=sha(payload), array_bytes=count*4,
                  selected_offsets_bytes_0based=offsets,
                  full_array_component_interval_checks=count,
                  schema_scope='contiguous N-by-6 displacement array, row order proven only by paired LOG matching')
    return report, values



