FROM python:3.12-slim-bookworm

WORKDIR /app

# System dependencies
# krb5-user / libgssapi-krb5-2: Kerberos client for SCCM Admin Service auth
#   (Invoke-RestMethod -Authentication Negotiate via GSSAPI on Linux pwsh)
# postgresql-client-16: pg_dump / pg_restore for Maintenance → Backups
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    krb5-user \
    libkrb5-3 \
    libgssapi-krb5-2 \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

# PowerShell Core – direct .deb from GitHub releases
# Using Debian 12 (bookworm) package – matches base image
RUN PWSH_VERSION=7.4.6 && \
    curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/powershell_${PWSH_VERSION}-1.deb_amd64.deb" \
    -o /tmp/powershell.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/powershell.deb && \
    rm /tmp/powershell.deb && \
    rm -rf /var/lib/apt/lists/*

# Enable OpenSSL legacy provider (required for NTLM/MD4 in ldap3 on Bookworm)
RUN printf 'openssl_conf = openssl_init\n\n[openssl_init]\nproviders = provider_sect\n\n[provider_sect]\ndefault = default_sect\nlegacy = legacy_sect\n\n[default_sect]\nactivate = 1\n\n[legacy_sect]\nactivate = 1\n' \
    > /etc/ssl/openssl_legacy.cnf
ENV OPENSSL_CONF=/etc/ssl/openssl_legacy.cnf

# NOTE: build context is the REPO ROOT (see docker-compose.yml / release.yml) so
# this image can bake the shared scripts/ folder that lives outside worker/.
# COPY paths are therefore repo-root-relative (worker/…, scripts/).

# Install Python dependencies
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker code
COPY worker/tasks/ ./tasks/

# PowerShell scripts (read at runtime by the dynamic/standalone runners). Baked in
# so a prebuilt-image install needs no repo checkout for them.
COPY scripts/ ./scripts/

EXPOSE 5555

# Default: celery worker (overridden in docker-compose for beat/flower)
CMD ["celery", "-A", "tasks", "worker", "--loglevel=info", "--concurrency=4"]
