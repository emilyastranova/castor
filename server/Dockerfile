# Built from the repository root (compose.yaml and CI set the context to `.`) so the image
# carries the built-in library: commands, playbooks, tools and themes. The root .dockerignore
# lets only those and the server package in.
FROM docker.io/library/python:3.12-slim
WORKDIR /app
# Pango (and fonts) for WeasyPrint, which renders timeline exports as PDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-roboto-unhinted fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY server/pyproject.toml ./
COPY server/castor_server ./castor_server
RUN pip install --no-cache-dir .
COPY commands /app/library/commands
COPY playbooks /app/library/playbooks
COPY tools /app/library/tools
COPY themes /app/library/themes
ENV CASTOR_DATA_DIR=/data \
    CASTOR_COMMANDS_DIR=/app/library/commands \
    CASTOR_PLAYBOOKS_DIR=/app/library/playbooks \
    CASTOR_TOOLS_DIR=/app/library/tools \
    CASTOR_THEMES_DIR=/app/library/themes
VOLUME ["/data"]
EXPOSE 8000
CMD ["castor-server"]
