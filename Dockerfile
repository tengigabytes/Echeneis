FROM python:3.11-slim

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends stress-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir .

COPY config/ config/
COPY benchmarks/ benchmarks/

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
ENV PYTHONPATH=/app

EXPOSE 4000

CMD ["uvicorn", "echeneis.gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "4000"]
