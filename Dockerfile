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

# Set working directory
WORKDIR /app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create necessary directories
RUN mkdir -p uploads logs

# Expose port (Railway uses PORT env var)
EXPOSE 8080

# Run the bot
CMD ["python", "bot.py"]
