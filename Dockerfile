FROM python:3.11-slim

# Install system dependencies including PHP and Node.js
RUN apt-get update && apt-get install -y \
    php-cli \
    nodejs \
    npm \
    curl \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set terminal environment variable
ENV TERM=xterm

# Set working directory
WORKDIR /app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt psutil

# Copy the rest of the application
COPY . .

# Create persistent data directory
RUN mkdir -p data/uploads data/logs

# Expose port (Railway uses PORT env var)
EXPOSE 8080

# Run the bot
CMD ["python", "bot.py"]
