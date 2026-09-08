"""Transport-only HTTP dispatcher for the shared M3 analysis contract."""

from __future__ import annotations

from collections.abc import Callable

from src.analysis.reader import AnalysisError
from src.analysis.service import AnalysisService

ROUTES = {
    '/api/analysis/versions': 'versions',
    '/api/analysis/performance': 'performance',
    '/api/analysis/compare-performance': 'compare-performance',
    '/api/analysis/evidence': 'evidence',
    '/api/analysis/compare-versions': 'compare-versions',
    '/api/analysis/rules': 'rules',
    '/api/analysis/diagnostics': 'diagnostics',
    '/api/analysis/scenario': 'scenario',
}

_ERROR_STATUS = {
    'NO_CLOSED_DATA': 404,
    'SESSION_NOT_FOUND': 404,
    'VERSION_NOT_FOUND': 404,
    'MONTH_REOPENED': 409,
    'VERSION_CHANGED': 409,
    'INCOMPATIBLE_RULES': 409,
    'SNAPSHOT_INVALID': 422,
}


def _validated(payload: dict, *, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(payload, dict):
        raise AnalysisError('INVALID_SCOPE', '请求JSON必须是对象')
    unexpected = sorted(set(payload) - allowed)
    missing = sorted(required - set(payload))
    if unexpected:
        raise AnalysisError('INVALID_SCOPE', f'请求包含不支持的参数：{", ".join(unexpected)}')
    if missing:
        raise AnalysisError('INVALID_SCOPE', f'请求缺少参数：{", ".join(missing)}')
    return payload


def _boolean(value, name: str) -> bool:
    if value in (True, 'true', '1'):
        return True
    if value in (False, 'false', '0', None, ''):
        return False
    raise AnalysisError('INVALID_SCOPE', f'{name} 必须是 true 或 false')


def _invoke(service: AnalysisService, call: Callable[[], dict]) -> tuple[int, dict]:
    try:
        result = call()
    except AnalysisError as exc:
        result = service._call(lambda: (_ for _ in ()).throw(exc))
    status = 200 if result['status'] == 'ok' else _ERROR_STATUS.get(
        result['error']['code'], 400)
    return status, result


def dispatch(service: AnalysisService, method: str, path: str,
             payload: dict | None = None) -> tuple[int, dict]:
    """Dispatch one fixed route. No route accepts database paths, SQL, or URLs."""
    route = ROUTES.get(path)
    if route is None:
        return 404, service._call(lambda: (_ for _ in ()).throw(
            AnalysisError('INVALID_SCOPE', '分析接口不存在')))
    data = payload or {}

    if route == 'versions':
        if method != 'GET':
            return _invoke(service, lambda: (_ for _ in ()).throw(
                AnalysisError('INVALID_SCOPE', '版本目录只接受GET请求')))
        return _invoke(service, lambda: service.list_close_versions(**_validated(
            data, allowed={'start_month', 'end_month', 'include_history'}) | {
                'include_history': _boolean(data.get('include_history'), 'include_history')
            }))

    if method != 'POST':
        return _invoke(service, lambda: (_ for _ in ()).throw(
            AnalysisError('INVALID_SCOPE', '该分析接口只接受POST JSON请求')))

    calls = {
        'performance': lambda: service.query_performance(**_validated(
            data, allowed={'scope', 'group_by', 'metrics', 'sort_by', 'sort_order', 'offset', 'limit'},
            required={'scope'})),
        'compare-performance': lambda: service.compare_performance(**_validated(
            data, allowed={'base_scope', 'current_scope', 'profit_metric'},
            required={'base_scope', 'current_scope'})),
        'evidence': lambda: service.get_session_evidence(**_validated(
            data, allowed={'close_ref', 'session_id', 'topic', 'mode', 'offset', 'limit'},
            required={'close_ref', 'session_id'})),
        'compare-versions': lambda: service.compare_close_versions(**_validated(
            data, allowed={'month', 'base_version', 'current_version', 'offset', 'limit'},
            required={'month', 'base_version', 'current_version'})),
        'rules': lambda: service.get_metric_rules(**_validated(
            data, allowed={'metric_keys', 'accounting_rule_version', 'analysis_rule_version'})),
        'diagnostics': lambda: service.diagnose_performance(**_validated(
            data, allowed={'scope', 'base_scope', 'rules', 'offset', 'limit'}, required={'scope'})),
        'scenario': lambda: service.simulate_scenario(**_validated(
            data, allowed={'close_ref', 'session_ids', 'assumptions', 'mode',
                           'assumption_source', 'authorization_scope'},
            required={'close_ref', 'session_ids', 'assumptions'})),
    }
    return _invoke(service, calls[route])
