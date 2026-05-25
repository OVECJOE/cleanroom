FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    android-tools-adb \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY cleanroom/ cleanroom/

RUN mkdir -p /var/lib/cleanroom

RUN useradd -r -m cleanroom && chown -R cleanroom:cleanroom /var/lib/cleanroom /app
USER cleanroom

EXPOSE 8000

CMD ["uvicorn", "cleanroom.main:app", "--host", "0.0.0.0", "--port", "8000"]