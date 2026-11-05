FROM docker.io/library/python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# The agent itself is bare: tools run in their own container images. It only needs the
# docker CLI to launch those as sibling containers through the mounted socket. On newer
# Debian releases (e.g. trixie), docker-cli is a separate package from docker.io.
RUN apt-get update && ( \
    apt-get install -y --no-install-recommends docker-cli ca-certificates \
    || apt-get install -y --no-install-recommends docker.io ca-certificates \
    ) && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY castor_agent ./castor_agent
RUN pip install --no-cache-dir .
CMD ["castor-agent"]
