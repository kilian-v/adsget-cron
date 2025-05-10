FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Install OS dependencies
RUN apt-get update && apt-get install -y curl wget gnupg2 libglib2.0-0 libnss3 libgconf-2-4 libfontconfig1 libxss1 libasound2 libatk-bridge2.0-0 libcups2 libxrandr2 libgtk-3-0

# Install pip dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Install playwright + Chromium
RUN playwright install --with-deps

# Expose port
EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
