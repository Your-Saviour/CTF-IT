FROM python:3.12.13-slim-trixie@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS test

COPY requirements-dev.txt requirements-agent.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt -r requirements-agent.txt

COPY . .

ENV PYTHONPATH=/app:/app/ai_agent

CMD ["python", "-m", "pytest", "tests/"]

FROM test AS acceptance

RUN apt-get update \
    && apt-get install -y --no-install-recommends wireguard-tools iproute2 iptables \
    && rm -rf /var/lib/apt/lists/*

CMD ["python", "-m", "pytest", "-q", "tests/aws_acceptance"]

FROM base AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends wireguard-tools iproute2 iptables \
    && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
