# syntax=docker/dockerfile:1

FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir /data \
    && chown app:app /data
COPY --from=builder /app/.venv /app/.venv
COPY sites.yaml /app/sites.yaml
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PRICE_MONITOR_CONFIG_PATH=/app/sites.yaml \
    PRICE_MONITOR_DB_PATH=/data/price_monitor.db \
    PRICE_MONITOR_LOG_FORMAT=json
WORKDIR /app
USER app
VOLUME ["/data"]
ENTRYPOINT ["price-monitor"]
CMD ["watch"]
