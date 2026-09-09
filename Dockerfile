FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates gnupg

# Install ClickHouse from the official APT repo (pre-extracted, no cold-start
# decompression penalty like the self-extracting static binary).
RUN curl -fsSL 'https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key' | \
      gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg] https://packages.clickhouse.com/deb stable main" \
      > /etc/apt/sources.list.d/clickhouse.list && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      clickhouse-server clickhouse-client && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The official mcp-clickhouse server goes in its OWN virtualenv, on purpose.
#
# google-adk pins mcp>=1.24,<2 (it imports mcp.shared.session), while
# mcp-clickhouse pulls fastmcp>=4 which requires mcp>=2. Those cannot coexist in
# one environment; pip resolves to ResolutionImpossible.
#
# They do not need to. MCP is a subprocess protocol: the server is launched over
# stdio and speaks JSON-RPC, so it only has to exist as an executable, not as an
# importable package in our interpreter. Isolating it keeps the server the real,
# official one while our app keeps the mcp version ADK needs.
RUN python -m venv /opt/mcp-clickhouse-venv && \
    /opt/mcp-clickhouse-venv/bin/pip install --no-cache-dir "mcp-clickhouse>=0.6" && \
    ln -s /opt/mcp-clickhouse-venv/bin/mcp-clickhouse /usr/local/bin/mcp-clickhouse

COPY . .

# Pre-create /tmp dirs for ClickHouse (entrypoint also does this, belt & suspenders)
RUN mkdir -p /tmp/clickhouse/{data,tmp,user_files,format_schemas,log}

# Name the server explicitly rather than letting the resolver hunt for it. The
# lookup works either way, but it probes each candidate by importing it, and there
# is no reason to pay that on every run when the path is known at build time.
ENV PORT=8080 CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8123 \
    MCP_TRANSCRIPT_PATH=/tmp/mcp_transcript.json \
    MCP_CLICKHOUSE_BIN=/opt/mcp-clickhouse-venv/bin/mcp-clickhouse
EXPOSE 8080
CMD ["bash", "docker-entrypoint.sh"]
