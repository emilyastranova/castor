FROM docker.io/library/python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# docker CLI lets the in-stack agent launch sibling containers via the mounted socket.
# On newer Debian releases (e.g. trixie), docker-cli is a separate package from docker.io.
RUN apt-get update && ( \
    apt-get install -y --no-install-recommends docker.io docker-cli ca-certificates git gcc python3-dev \
    || apt-get install -y --no-install-recommends docker.io ca-certificates git gcc python3-dev \
    ) && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir git+https://github.com/Pennyw0rth/NetExec && (nxc --version || true)
COPY pyproject.toml ./
COPY castor_agent ./castor_agent
RUN pip install --no-cache-dir .
CMD ["castor-agent"]
