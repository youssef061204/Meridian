FROM python:3.11.11-slim-bookworm@sha256:081075da77b2b55c23c088251026fb69a7b2bf92471e491ff5fd75c192fd38e5
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 SPARK_LOCAL_IP=127.0.0.1
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless procps curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml /app/
COPY requirements.lock /app/
COPY src /app/src
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY . /app
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "meridian.cli", "--help"]
