FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY wraith_mcp/ wraith_mcp/

RUN pip install --no-cache-dir . && patchright install chromium

ENV HEADLESS=true
EXPOSE 8808

ENTRYPOINT ["wraith-mcp"]
CMD ["--transport", "stdio"]
