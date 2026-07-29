"""抖音直播带货 ROI 算账 Agent — MCP Server 入口.

启动方式:
    mcp run src/server.py
    或
    python src/server.py
"""

from mcp.server.mcpserver import MCPServer

from src.tools.import_tools import register_import_tools
from src.tools.calc_tools import register_calc_tools
from src.tools.report_tools import register_report_tools

# 创建 MCP Server
server = MCPServer("livecommerce-roi-agent")

# 注册全部工具
register_import_tools(server)
register_calc_tools(server)
register_report_tools(server)


if __name__ == "__main__":
    server.run(transport="stdio")
