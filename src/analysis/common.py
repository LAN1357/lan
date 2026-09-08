"""Shared validation and presentation helpers for the M3 read-only API."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.analysis.schemas import MAX_LIMIT
from src.analysis.reader import AnalysisError


def money(value: int) -> dict:
    return {
        'cents': value,
        'yuan_text': f'{Decimal(value) / 100:,.2f}',
        'currency': 'CNY',
    }


def ratio_value(value: str | None, *, reason: str = '分母不大于0') -> dict:
    if value is None:
        return {'decimal': None, 'percent_text': None, 'not_applicable_reason': reason}
    percent = (Decimal(value) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {'decimal': value, 'percent_text': f'{percent}%'}


def paging(total: int, offset: int, limit: int) -> dict:
    if type(offset) is not int or offset < 0:
        raise AnalysisError('INVALID_SCOPE', 'offset 必须是非负整数')
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise AnalysisError('INVALID_SCOPE', f'limit 必须是 1 至 {MAX_LIMIT} 的整数')
    returned = max(0, min(limit, total - offset))
    return {
        'total_count': total,
        'returned_count': returned,
        'offset': offset,
        'limit': limit,
        'has_more': offset + returned < total,
        'next_offset': offset + returned if offset + returned < total else None,
    }


def error_result(exc: AnalysisError) -> dict:
    return exc.payload()
