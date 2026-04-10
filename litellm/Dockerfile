FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir 'litellm[proxy,extra_proxy]' 'langfuse>=2.0.0,<3.0.0' && \
    cd /usr/local/lib/python3.11/site-packages/litellm_proxy_extras && \
    prisma generate --schema=schema.prisma

WORKDIR /app
COPY config.yaml /app/config.yaml

EXPOSE 4000

ENTRYPOINT ["litellm"]
CMD ["--config", "/app/config.yaml", "--port", "4000", "--detailed_debug"]
