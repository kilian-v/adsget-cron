FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Minimal OS tooling. The browser's own system dependencies are installed by
# `playwright install --with-deps` below — hand-listing them broke on modern
# Debian, where libgconf-2-4 no longer exists.
# libatomic1 is not optional: Playwright's bundled Node driver links against
# libatomic.so.1, which python:3.12-slim does not carry. Without it the driver
# dies in the dynamic linker, so `playwright install --with-deps` cannot even
# start to install the browser dependencies itself. The step then exits 127 --
# the same code a missing command returns, which is what made this take three
# failed builds to identify in the sibling service.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Install pip dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium + its system deps. Done before copying the source so that a
# code change does not re-download the browser on every build.
RUN python -m playwright install --with-deps chromium

# Copy project files
COPY . .

# Expose port
EXPOSE 8000

# Start FastAPI
# Honour $PORT where the platform sets one; 8000 locally. A hardcoded port here
# means the host routes to a port nothing listens on, which surfaces as a 502.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
