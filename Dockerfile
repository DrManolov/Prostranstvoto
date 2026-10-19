FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
RUN --mount=type=secret,id=proxy_ca,required=false if [ -f /run/secrets/proxy_ca ]; then cp /run/secrets/proxy_ca /usr/local/share/ca-certificates/cloud-proxy.crt; update-ca-certificates; fi
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update -o APT::Update::Error-Mode=any && apt-get install -y --no-install-recommends libreoffice-writer fonts-liberation fonts-crosextra-carlito fonts-dejavu-core postgresql-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m -u 1000 pfplus && mkdir -p /app/runtime && chown pfplus:pfplus /app /app/runtime
COPY --chown=pfplus:pfplus . .
USER pfplus
EXPOSE 8000
CMD ["sh", "scripts/container-start.sh"]
