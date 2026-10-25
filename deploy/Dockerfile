FROM python:3.11-slim
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
# Listens on the host's loopback only (run with --network host); the site's reverse proxy is the only way in.
ENV SHIJI_HOST=127.0.0.1 SHIJI_PORT=8025
CMD ["uv", "run", "--no-sync", "shiji"]
