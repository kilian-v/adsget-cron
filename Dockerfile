FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Minimal OS tooling. The browser's own system dependencies are installed by
# `playwright install --with-deps` below — hand-listing them broke on modern
# Debian, where libgconf-2-4 no longer exists.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install pip dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium + its system deps. Done before copying the source so that a
# code change does not re-download the browser on every build.
RUN playwright install --with-deps chromium

# Copy project files
COPY . .

# Expose port
EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
