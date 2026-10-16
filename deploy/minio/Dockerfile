# Build MinIO's patched community release from the upstream source release.
FROM golang:1.26-alpine AS build
ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z
RUN apk add --no-cache git
RUN CGO_ENABLED=0 GOBIN=/out go install github.com/minio/minio@${MINIO_VERSION}
RUN CGO_ENABLED=0 GOBIN=/out go install \
    -ldflags "-s -w -X github.com/minio/minio/cmd.ReleaseTag=${MINIO_VERSION}" \
    github.com/minio/minio@${MINIO_VERSION}

FROM alpine:3.23
LABEL org.opencontainers.image.source="https://github.com/Albert-Li-Sz/Nefex-Web-UI" \
      org.opencontainers.image.url="https://github.com/minio/minio" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="RELEASE.2025-10-15T17-29-55Z"
RUN apk add --no-cache ca-certificates curl tini \
    && addgroup -g 10001 minio \
    && adduser -D -u 10001 -G minio minio \
    && mkdir /data && chown minio:minio /data
COPY --from=build /out/minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["/sbin/tini", "--", "minio"]
CMD ["server", "/data", "--console-address", ":9001"]
