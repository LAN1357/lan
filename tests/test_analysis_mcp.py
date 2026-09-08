import asyncio

from src.analysis.mcp_server import create_server


EXPECTED = {
    'workbench_list_close_versions', 'workbench_query_performance',
    'workbench_compare_performance', 'workbench_get_session_evidence',
    'workbench_compare_close_versions', 'workbench_get_metric_rules',
    'workbench_diagnose_performance', 'workbench_simulate_scenario',
}


def test_server_exposes_exactly_eight_readonly_tools(closed_db):
    path, _conn = closed_db

    async def inspect_tools():
        tools = await create_server(path).list_tools()
        assert {tool.name for tool in tools} == EXPECTED
        assert all(tool.annotations.readOnlyHint is True for tool in tools)
        assert all(tool.annotations.destructiveHint is False for tool in tools)
        assert all(tool.annotations.openWorldHint is False for tool in tools)
        assert all('path' not in tool.inputSchema.get('properties', {}) for tool in tools)
        assert all('sql' not in tool.inputSchema.get('properties', {}) for tool in tools)

    asyncio.run(inspect_tools())


def test_tool_returns_structured_error_without_path_or_trace(tmp_path):
    missing = tmp_path / 'secret-name.db'

    async def invoke():
        _content, result = await create_server(missing).call_tool('workbench_list_close_versions', {})
        assert result['status'] == 'error'
        text = str(result)
        assert 'secret-name.db' not in text
        assert 'Traceback' not in text

    asyncio.run(invoke())
