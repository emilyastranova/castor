FROM docker.io/library/python:3.12-slim
WORKDIR /app
# Pango (and fonts) for WeasyPrint, which renders timeline exports as PDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-roboto-unhinted fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY castor_server ./castor_server
RUN pip install --no-cache-dir .
VOLUME ["/data"]
EXPOSE 8000
CMD ["castor-server"]
