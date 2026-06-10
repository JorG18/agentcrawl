FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENTCRAWL_DB=/data/agentcrawl.db \
    AGENTCRAWL_FETCHER=http \
    AGENTCRAWL_AUTH_ENABLED=true \
    AGENTCRAWL_ALLOW_LOCAL_FILES=false \
    AGENTCRAWL_ALLOW_PRIVATE_NETWORK=false

WORKDIR /app

LABEL org.opencontainers.image.title="AgentCrawl" \
      org.opencontainers.image.description="Self-hosted web extraction and Markdown crawling for AI agents." \
      org.opencontainers.image.source="https://github.com/JorG18/agentcrawl" \
      org.opencontainers.image.licenses="Apache-2.0"

COPY pyproject.toml README.md LICENSE ./
COPY agentcrawl ./agentcrawl

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e ".[server,mcp]" \
    && useradd --create-home --uid 10001 agentcrawl \
    && mkdir -p /data \
    && chown -R agentcrawl:agentcrawl /app /data

USER agentcrawl

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["uvicorn", "agentcrawl.server:app", "--host", "0.0.0.0", "--port", "8000"]
