FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8501

CMD ["streamlit", "run", "app/Painel.py", "--server.port=8501", "--server.address=0.0.0.0"]
