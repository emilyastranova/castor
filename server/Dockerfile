FROM docker.io/library/python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY castor_server ./castor_server
RUN pip install --no-cache-dir .
VOLUME ["/data"]
EXPOSE 8000
CMD ["castor-server"]
