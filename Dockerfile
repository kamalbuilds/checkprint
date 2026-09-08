FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates

# ClickHouse runs inside the container so the deployed service is self-contained.
# The track permits ClickHouse Cloud or self-hosted; this is self-hosted.
# Set CLICKHOUSE_HOST/PASSWORD to a Cloud endpoint to use Cloud instead: no code change,
# the entrypoint skips the local server whenever CLICKHOUSE_HOST is not localhost.
RUN curl -sSL -o /usr/local/bin/clickhouse \
      "https://builds.clickhouse.com/master/amd64/clickhouse" \
 && chmod +x /usr/local/bin/clickhouse

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080 CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8123
EXPOSE 8080
CMD ["bash", "docker-entrypoint.sh"]
