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

COPY . .

# Pre-create /tmp dirs for ClickHouse (entrypoint also does this, belt & suspenders)
RUN mkdir -p /tmp/clickhouse/{data,tmp,user_files,format_schemas,log}

ENV PORT=8080 CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8123
EXPOSE 8080
CMD ["bash", "docker-entrypoint.sh"]
