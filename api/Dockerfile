FROM python:3.12-slim-bookworm

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Enable OpenSSL legacy provider (required for NTLM/MD4 in ldap3 on Bookworm)
RUN printf 'openssl_conf = openssl_init\n\n[openssl_init]\nproviders = provider_sect\n\n[provider_sect]\ndefault = default_sect\nlegacy = legacy_sect\n\n[default_sect]\nactivate = 1\n\n[legacy_sect]\nactivate = 1\n' \
    > /etc/ssl/openssl_legacy.cnf
ENV OPENSSL_CONF=/etc/ssl/openssl_legacy.cnf

# NOTE: build context is the REPO ROOT (see docker-compose.yml / release.yml) so
# this image can bake the shared locales/ and scripts/ folders that live outside
# api/. COPY paths are therefore repo-root-relative (api/…, locales/, scripts/).

# Install Python dependencies
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api/app/ ./app/
COPY LICENSE ./LICENSE

# Portal i18n locales (served at /locales/*.json) and PowerShell seed scripts
# (migration 0046 seeds script_modules from these on a fresh DB). Baked in so a
# prebuilt-image install needs no repo checkout for them.
COPY locales/ ./locales/
COPY scripts/ ./scripts/

# Version (passed from docker-compose via build arg)
ARG APP_VERSION=0.0.0
ENV APP_VERSION=${APP_VERSION}

# Alembic (migrations)
COPY api/alembic/ ./alembic/
COPY api/alembic.ini .

EXPOSE 8000

# --proxy-headers + --forwarded-allow-ips=* so uvicorn honors nginx's
# X-Forwarded-Proto/-For (TLS is terminated at the proxy). Without this the app
# sees plain http and derives http:// OIDC redirect URIs, breaking SSO callbacks.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
