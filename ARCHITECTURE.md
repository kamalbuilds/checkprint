# Checkprint: partner wiring

One product, one Devpost track: **ClickHouse**. Google ADK + Gemini do the agent work. The partner the form selects is the official `mcp-clickhouse` MCP server.

## Runtime path

```
browser  GET /api/catalog
    ->  web/server.py catalog()
    ->  qc/mcp_store.py catalog_via_mcp()
    ->  mcp-clickhouse stdio  run_query
    ->  ClickHouse  deliverable.catalog_status
```

The page a judge loads reads the catalog through MCP. Inserts of 100ms loudness samples stay on `clickhouse-connect` because `run_query` is not an insert path.

ADK supervisor (`agent/supervisor.py`) holds the same `McpToolset` and composes its own SQL when a title is reviewed.

## Where it is in code

| Piece | File |
|---|---|
| MCP catalog read | `qc/mcp_store.py` `catalog_via_mcp` |
| HTTP surface | `web/server.py` `GET /api/catalog` returns `via: mcp-clickhouse` |
| MCP transcript | `GET /api/mcp-transcript` |
| ADK toolset | `agent/supervisor.py` `McpToolset` over `mcp-clickhouse` |
| Measure / repair | `qc/measure.py` ffmpeg ebur128, then re-measure |

## Google

Gemini on Vertex / API. ADK `LlmAgent` with ClickHouse tools. No partner is decorative: if MCP is down the payload says so.

Live: catalog JSON is the proof a ClickHouse judge can fetch without our word for it.
