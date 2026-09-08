"""
Test: fails if the MCP path is not actually used.

Verifies that catalog_via_mcp() actually invokes the official mcp-clickhouse
server (stdio transport) by checking the transcript it writes.

Rules:
1. transcript file must exist and be non-empty
2. at least one entry must be a run_query call
3. at least one run_query must reference catalog_status SQL
4. run_query results must parse to non-empty rows
5. catalog rows must have the expected column set

Run from project root:
    uv run --python 3.12 python -m pytest tests/test_mcp_path.py -v
or directly:
    uv run --python 3.12 python tests/test_mcp_path.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a standalone script from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from qc.mcp_store import _TRANSCRIPT_PATH, catalog_via_mcp

REQUIRED_COLS = {"title_id", "title", "last_run", "failures_before", "failures_after", "verdict"}


def _parse_rows(result_text: str) -> list:
    try:
        data = json.loads(result_text)
        if isinstance(data, dict) and "columns" in data and "rows" in data:
            return [dict(zip(data["columns"], row)) for row in data["rows"]]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def test_mcp_path_is_used() -> None:
    """Full integration test: calls catalog_via_mcp() and validates transcript."""
    # Run the real MCP path
    catalog_rows = catalog_via_mcp()

    # --- Load transcript written by catalog_via_mcp() ---
    assert _TRANSCRIPT_PATH.exists(), \
        f"FAIL: transcript file not written at {_TRANSCRIPT_PATH}"
    transcript = json.loads(_TRANSCRIPT_PATH.read_text())

    # Rule 1: non-empty
    assert len(transcript) > 0, "FAIL: transcript is empty — MCP server was never called"

    # Rule 2: at least one run_query
    run_query_calls = [t for t in transcript if t.get("tool") == "run_query"]
    assert len(run_query_calls) > 0, \
        f"FAIL: no run_query tool calls. Tools used: {[t['tool'] for t in transcript]}"

    # Rule 3: catalog_status SQL present
    catalog_calls = [
        t for t in run_query_calls
        if "catalog_status" in t.get("args", {}).get("query", "")
    ]
    assert len(catalog_calls) > 0, (
        f"FAIL: no run_query with catalog_status SQL.\n"
        f"  Queries: {[t['args'].get('query','') for t in run_query_calls]}"
    )

    # Rule 4: results parse to non-empty rows
    for call in run_query_calls:
        rows = _parse_rows(call.get("result_preview", ""))
        assert len(rows) > 0, (
            f"FAIL: run_query returned 0 rows.\n"
            f"  Query: {call['args'].get('query','')[:120]}"
        )

    # Rule 5: catalog rows have expected columns
    assert len(catalog_rows) > 0, "FAIL: catalog_via_mcp() returned 0 rows"
    actual_cols = set(catalog_rows[0].keys())
    missing = REQUIRED_COLS - actual_cols
    assert not missing, \
        f"FAIL: catalog rows missing columns {missing}. Got: {actual_cols}"

    # Summary
    print(f"\n  transcript entries:   {len(transcript)}")
    print(f"  run_query calls:      {len(run_query_calls)}")
    print(f"  catalog_status calls: {len(catalog_calls)}")
    print(f"  catalog rows:         {len(catalog_rows)}")


if __name__ == "__main__":
    print("Running MCP path test (real subprocess)...\n")
    try:
        test_mcp_path_is_used()
        print("\n=== ALL CHECKS PASSED ===")
    except AssertionError as e:
        print(f"\n=== TEST FAILED ===\n{e}")
        sys.exit(1)
