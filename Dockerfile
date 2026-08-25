FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# tgcrypto needs a C compiler to build if no matching wheel is available for
# this platform. Installed, used, then removed in the same layer so the
# final image stays small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc \
    && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["python3", "bot.py"]
