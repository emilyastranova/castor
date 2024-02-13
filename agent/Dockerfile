FROM python:3.10.13-bookworm

# Copy source code
COPY agent /app
COPY castor_server/dependencies/castor_lib /app/castor_lib

# Set working directory
WORKDIR /app

# Install dependencies
RUN pip install -r requirements.txt

# Install castor-lib
RUN pip install ./castor_lib

# Run the application
CMD ["python", "agent.py"]