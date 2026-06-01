FROM python:3.11-slim

# Install system dependencies including PHP and Node.js
RUN apt-get update && apt-get install -y \
    php-cli \
    nodejs \
    npm \
    curl \
    wget \
    git \
    procps \
    && rm -rf /var/lib/apt/lists/*

ENV TERM=xterm
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data
RUN mkdir -p data/uploads data/logs

# Render uses $PORT (defaults 10000)
EXPOSE 10000

# Start the web panel (which auto-starts the bot)
CMD ["python", "-u", "web_panel.py"]
