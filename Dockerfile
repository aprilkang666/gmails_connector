# Remote mail connector (claude.ai custom connector).
# Mount a volume at /data holding config.json and tokens.db (created locally
# with gmail-mcp-setup), and set CONNECTOR_BASE_URL + CONNECTOR_PASSWORD.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV GMAIL_MCP_HOME=/data \
    GMAIL_MCP_CONFIG=/data/config.json \
    HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "gmail_mcp.remote"]
