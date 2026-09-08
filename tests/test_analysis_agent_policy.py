from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_codex_role_grants_analysis_autonomy_but_not_business_writes():
    role_path = ROOT / '.codex/agents/readonly-review-analyst.toml'
    role = tomllib.loads(role_path.read_text(encoding='utf-8'))
    instructions = role['developer_instructions']

    assert '自主规划分析步骤' in instructions
    assert '连续调用工具' in instructions
    assert '待验证假设' in instructions
    assert 'assumption_source=agent_proposed' in instructions
    assert 'authorization_scope' in instructions
    assert '来源标记当作授权证明' in instructions
    assert '不修改正式数据' in role['description']
    assert '导入、成本配置、关账' in instructions

    for legacy_policy in ('净利率低于5%', '投流占比高于40%', 'configure_costs', 'import_orders'):
        assert legacy_policy not in instructions


def test_claude_role_matches_the_same_active_readonly_boundary():
    role_path = ROOT / '.claude/agents/roi-analyst.md'
    contents = role_path.read_text(encoding='utf-8')

    assert 'name: livecommerce-review-analyst' in contents
    assert 'tools: mcp:livecommerce-m3-readonly' in contents
    assert '自主规划并连续完成' in contents
    assert '待验证假设' in contents
    assert 'assumption_source=agent_proposed' in contents
    assert 'authorization_scope' in contents
    assert '来源标记只能追溯，不能证明用户授权' in contents
    assert '不导入、改成本、改归属、确认费用、关账、重开' in contents

    for legacy_tool in ('configure_costs', 'import_orders', 'calculate_roi'):
        assert legacy_tool not in contents
