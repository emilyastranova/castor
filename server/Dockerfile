# Built from the repository root (compose.yaml and CI set the context to `.`) so the image
# carries the built-in library: commands, playbooks, tools and themes, and the playbook spec the
# AI reads. The root .dockerignore lets only those and the server package in.
FROM docker.io/library/python:3.12-slim
WORKDIR /app
# Pango (and fonts) for WeasyPrint, which renders timeline exports as PDF; git and the OpenSSH
# client for plugin repositories (plugin_repos.py).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-roboto-unhinted fonts-dejavu-core \
      git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY server/pyproject.toml ./
COPY server/castor_server ./castor_server
RUN pip install --no-cache-dir .
COPY commands /app/library/commands
COPY playbooks /app/library/playbooks
COPY tools /app/library/tools
COPY themes /app/library/themes
# The playbook format, which the AI reads when it writes or fixes a playbook.
COPY docs/PLAYBOOK_SPEC.md /app/library/docs/PLAYBOOK_SPEC.md
ENV CASTOR_DATA_DIR=/data \
    CASTOR_COMMANDS_DIR=/app/library/commands \
    CASTOR_PLAYBOOKS_DIR=/app/library/playbooks \
    CASTOR_TOOLS_DIR=/app/library/tools \
    CASTOR_THEMES_DIR=/app/library/themes \
    CASTOR_DOCS_DIR=/app/library/docs
VOLUME ["/data"]
EXPOSE 8000
CMD ["castor-server"]
