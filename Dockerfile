FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

COPY config/ config/

EXPOSE 4000

CMD ["uvicorn", "echeneis.gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "4000"]
