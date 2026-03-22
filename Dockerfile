FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VPN_BOT_BASE_DIR=/opt/vpn-manager-bot

WORKDIR /opt/vpn-manager-bot

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        openssh-client \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt

RUN pip install -r requirements.txt

COPY app ./app

RUN mkdir -p /opt/vpn-manager-bot/data

WORKDIR /opt/vpn-manager-bot/app

CMD ["sh", "-c", "python manage_db.py init && python main.py"]
