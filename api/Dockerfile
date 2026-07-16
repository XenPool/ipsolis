# ── Stage 1: UI assets (air-gap) ────────────────────────────────────────────
# Compiles Tailwind CSS at build time and vendors htmx / Monaco / Inter locally,
# so the RUNTIME loads nothing from external CDNs. Internet is used only here
# (during the image build). Build context is the repo root (see docker-compose).
FROM node:20-alpine AS assets
WORKDIR /build
COPY api/package.json ./
RUN npm install --no-audit --no-fund
# Sources the Tailwind CLI scans: the compiled config + the templates AND the
# Python route files (some Tailwind class strings are assembled in Python).
COPY api/tailwind.config.js ./tailwind.config.js
COPY api/static-src ./static-src
COPY api/app/templates ./app/templates
COPY api/app/routes ./app/routes
RUN mkdir -p /out/css /out/vendor/htmx /out/vendor/monaco /out/vendor/fonts \
 && npx tailwindcss -i static-src/input.css -o /out/css/app.css --minify \
 && cp node_modules/htmx.org/dist/htmx.min.js /out/vendor/htmx/htmx.min.js \
 && cp -r node_modules/monaco-editor/min/vs /out/vendor/monaco/vs \
 && cp node_modules/@fontsource/inter/files/inter-latin-400-normal.woff2 /out/vendor/fonts/inter-400.woff2 \
 && cp node_modules/@fontsource/inter/files/inter-latin-500-normal.woff2 /out/vendor/fonts/inter-500.woff2 \
 && cp node_modules/@fontsource/inter/files/inter-latin-600-normal.woff2 /out/vendor/fonts/inter-600.woff2 \
 && cp node_modules/@fontsource/inter/files/inter-latin-700-normal.woff2 /out/vendor/fonts/inter-700.woff2

# ── Stage 2: application image ──────────────────────────────────────────────
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
# Air-gap UI assets from stage 1 (compiled Tailwind CSS + vendored
# htmx/Monaco/Inter). AFTER the app COPY so it lands in app/static/ without
# being overwritten. At runtime the browser loads only these local files.
COPY --from=assets /out/ ./app/static/
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
