# Reuse the full official runtime, Python dependencies, local models and Pyodide.
# Only the changed application/frontend and Chromium are built for this fork.
ARG WEBUI_BASE_IMAGE=ghcr.io/open-webui/open-webui:v0.12.3@sha256:a537bbe1e6b428820f52ed4a029d7fff97062e24d5cf32c365fba36fd0542def
FROM ${WEBUI_BASE_IMAGE} AS upstream

FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS frontend
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --force
COPY src ./src
COPY static ./static
COPY scripts/encryption ./scripts/encryption
COPY scripts/build-info.js ./scripts/build-info.js
COPY crypto ./crypto
COPY svelte.config.js vite.config.ts tsconfig.json tailwind.config.js postcss.config.js ./
# Keep the upstream offline Python wheels; no duplicate Pyodide downloads.
COPY --from=upstream /app/build/pyodide ./static/pyodide
ARG BUILD_HASH=dev-build
ARG BUILD_CHANNEL=custom
ARG BUILD_NODE_OPTIONS=--max-old-space-size=6144
ENV APP_BUILD_HASH=${BUILD_HASH} APP_BUILD_CHANNEL=${BUILD_CHANNEL} NODE_OPTIONS=${BUILD_NODE_OPTIONS}
RUN node -e "const fs=require('fs'); const expected=require('./package.json').dependencies.pyodide.replace('^',''); const actual=require('./static/pyodide/package.json').version; if(expected!==actual) throw Error('Upstream Pyodide version mismatch'); const lock=require('./static/pyodide/pyodide-lock.json'); for(const key of ['numpy','pandas','matplotlib','scipy']) { if(!fs.existsSync('static/pyodide/'+lock.packages[key].file_name)) throw Error('Missing bundled wheel: '+key); }"
RUN npm run build:container

FROM upstream AS runtime
USER root:root
WORKDIR /app/backend
ENV WEBUI_NAME="Nefex Web UI" PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
COPY backend/requirements.txt /tmp/nefex-requirements.txt
RUN python -c "from pathlib import Path; assert Path('requirements.txt').read_bytes() == Path('/tmp/nefex-requirements.txt').read_bytes(), 'Changed Python dependencies: use deploy/Dockerfile.source'" \
    && rm /tmp/nefex-requirements.txt
# Replace the whole application tree so retired modules cannot survive the overlay.
RUN rm -rf /app/build /app/backend/open_webui \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright \
    && rm -rf /var/lib/apt/lists/*
COPY backend ./
COPY --from=frontend /app/build /app/build
COPY package.json CHANGELOG.md LICENSE LICENSE_HISTORY LICENSE_NOTICE LICENSE_NEFEX /app/
ARG BUILD_HASH=dev-build
ENV WEBUI_BUILD_HASH=${BUILD_HASH}
LABEL org.opencontainers.image.title="Nefex Web UI" \
      org.opencontainers.image.source="https://github.com/Albert-Li-Sz/Nefex-Web-UI" \
      org.opencontainers.image.licenses="LicenseRef-Open-WebUI" \
      org.opencontainers.image.revision=${BUILD_HASH}
EXPOSE 8080
HEALTHCHECK CMD python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://localhost:8080/health',timeout=5))['status']"
CMD ["bash", "start.sh"]
