from src.analysis.diagnostics import diagnose_performance
from src.analysis.reader import CloseReader


SCOPE = {'close_refs': [{'month': '2026-09', 'version': 1}]}


def test_diagnostics_use_fixed_rules_without_scores(closed_db):
    path, _conn = closed_db
    result = diagnose_performance(CloseReader(path), SCOPE, rules=['major_components'], limit=1)
    assert result['data']['paging']['total_count'] == 2
    assert result['data']['paging']['has_more'] is True
    item = result['data']['items'][0]
    assert item['rule_id'] == 'major_components'
    assert 'facts' in item and 'checks_for_bp' in item
    assert 'score' not in item
    assert result['data']['summary']['attention_session_count'] is None
    assert result['data']['summary']['attention_status'] == 'incomplete'
    assert result['data']['summary']['final_loss']['status'] == 'not_run'
    assert result['data']['summary']['final_loss']['count'] is None
    assert result['data']['summary']['major_components_counted_as_attention'] is False
    amounts = [abs(x['amount']['cents']) for x in item['facts']['components']]
    assert amounts == sorted(amounts, reverse=True)


def test_period_change_requires_explicit_base_and_returns_facts(closed_db):
    path, _conn = closed_db
    reader = CloseReader(path)
    no_base = diagnose_performance(reader, SCOPE, rules=['period_change'])
    assert no_base['data']['items'] == []
    result = diagnose_performance(
        reader, SCOPE | {'session_ids': ['S1']},
        base_scope=SCOPE | {'session_ids': ['S2']}, rules=['period_change'])
    item = result['data']['items'][0]
    assert item['rule_id'] == 'period_change'
    assert item['facts']['final_profit_delta']['cents'] == -76000
    assert item['checks_for_bp']


def test_attention_summary_deduplicates_overlapping_rules(closed_db):
    path, conn = closed_db
    import json
    from src.engine.session_profit import AMOUNT_FIELDS, calculate_session

    row = conn.execute('SELECT snapshot FROM closes').fetchone()
    snapshot = json.loads(row[0])
    amounts = {key: snapshot['results']['S1'][key] for key in AMOUNT_FIELDS}
    amounts['management'] += 200000
    snapshot['results']['S1'] = calculate_session(amounts)
    conn.execute('UPDATE closes SET snapshot=?', (json.dumps(snapshot),))

    result = diagnose_performance(
        CloseReader(path), SCOPE,
        rules=['final_loss', 'operating_profit_final_loss', 'major_components'],
    )
    summary = result['data']['summary']
    assert summary['final_loss'] == {'status': 'executed', 'count': 1}
    assert summary['operating_profit_final_loss'] == {
        'status': 'executed', 'count': 1, 'subset_of': 'final_loss'}
    assert summary['attention_session_count'] == 1
    assert summary['attention_status'] == 'complete'


def test_loss_details_are_ranked_before_paging_and_summary_stays_full_scope(closed_db):
    path, conn = closed_db
    import json
    from src.engine.session_profit import AMOUNT_FIELDS, calculate_session

    snapshot = json.loads(conn.execute('SELECT snapshot FROM closes').fetchone()[0])
    source_session = snapshot['sessions']['S1']
    source_result = snapshot['results']['S1']
    sessions, results = {}, {}
    for index in range(25):
        sid = f'Z{index:02d}'
        sessions[sid] = source_session | {'talent': f'达人{index:02d}'}
        amounts = {key: source_result[key] for key in AMOUNT_FIELDS}
        amounts['management'] += 300000 + index * 100
        results[sid] = calculate_session(amounts)
    snapshot['sessions'] = sessions
    snapshot['results'] = results
    conn.execute('UPDATE closes SET snapshot=?', (json.dumps(snapshot),))

    first = diagnose_performance(CloseReader(path), SCOPE, rules=['final_loss'], limit=20)
    second = diagnose_performance(CloseReader(path), SCOPE, rules=['final_loss'], offset=20, limit=20)
    assert first['data']['paging']['total_count'] == 25
    assert first['data']['summary']['final_loss']['count'] == 25
    assert first['data']['items'][0]['session_id'] == 'Z24'
    assert second['data']['paging']['total_count'] == 25
    assert second['data']['summary']['final_loss']['count'] == 25
    assert {x['session_id'] for x in first['data']['items']}.isdisjoint(
        {x['session_id'] for x in second['data']['items']})
