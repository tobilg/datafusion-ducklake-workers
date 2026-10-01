FROM scratch
COPY minio /minio
ENTRYPOINT ["/minio", "--config-dir", "/config", "server", "--address", ":9000", "/data"]
