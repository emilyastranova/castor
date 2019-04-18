FROM python:3.10.13-bookworm

# Copy source code
COPY . /app
# Set working directory
WORKDIR /app

# Install dependencies
RUN pip install -r requirements.txt

# Run the application
CMD ["python", "worker.py"]