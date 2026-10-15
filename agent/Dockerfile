FROM docker.io/library/python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# docker CLI lets the in-stack agent launch sibling containers via the mounted socket.
RUN apt-get update && apt-get install -y --no-install-recommends docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY castor_agent ./castor_agent
RUN pip install --no-cache-dir .
CMD ["castor-agent"]
