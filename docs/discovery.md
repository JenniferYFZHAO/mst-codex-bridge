# 专用工具检索记录（2026-10-28）

目标为网架/空间结构分析设计软件 MST2008、MstCAD.exe。检索没有找到可以直接采用且已核实属于该软件的公开 MCP、Skill 或 Python 控制 SDK。此结论只覆盖本次检索范围，不能证明所有公开或私有工具都不存在。

|渠道|检索内容|本次结果|
|---|---|---|
|Codex 插件目录搜索|`MST2008 MstCAD 空间结构`|返回插件列表为空；目录结果非穷尽|
|GitHub 连接器仓库搜索|`"MstCAD"`、`"MST2008"`|两次返回仓库列表为空|
|Web|`"MST2008" "MCP"`；`"MstCAD" Python API`；`"MST" "空间结构" "skill"`|未找到该结构软件的可验证控制实现|
|Web|`"MST2008" 自动化 接口 Python`；`"空间结构" "MST" "MCP" github`|出现工程应用及多类同名结果，未得到专用接口实现|

MST 最小生成树算法与此工具无关；Atomera 的 [MSTcad 官方说明](https://atomera.com/technology-mstcad/)针对半导体 TCAD，也不能作为结构 MST2008 的接口证据。没有依赖第三方下载站安装软件或工具。

采用的桥接技术依据：

- [官方 MCP Python SDK v1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)：本包锁定 v1 兼容范围，实测版本 1.30.0。官方文档另有 v2，后续升级需重新验证。
- [官方 stdio 客户端示例](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/client.md)：以真实子进程完成 initialize、list_tools 和 call_tool。
- [MCP 服务端开发文档](https://modelcontextprotocol.io/docs/develop/build-server)：协议输出与日志分离。
- [Python venv](https://docs.python.org/3/library/venv.html)：依赖安装在项目 .venv 中。

MST 格式适配依据本机说明书和先前真实导入/求解/保存/导出探针；这些材料未被当作公网 API 文档。CLI 参数按本机 `codex mcp add --help` 核验；本轮未修改 Codex 全局配置。
