FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user; instance/ (SQLite) and logs/ are the only writable folders
RUN chmod +x docker-entrypoint.sh \
 && useradd --system --create-home --uid 10001 gateway \
 && mkdir -p /app/instance /app/logs \
 && chown -R gateway:gateway /app/instance /app/logs
USER gateway

EXPOSE 5000
# Only meaningful for the web role; the worker service switches it off in compose
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4)" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["app"]
