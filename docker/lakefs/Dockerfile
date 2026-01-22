FROM treeverse/lakefs:latest

USER root

RUN if command -v apk >/dev/null 2>&1; then \
        apk add --no-cache postgresql-client; \
    elif command -v apt-get >/dev/null 2>&1; then \
        apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*; \
    else \
        echo "No supported package manager for postgresql-client"; \
    fi
