FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080

WORKDIR /app

COPY requirements-site.txt /app/requirements-site.txt
RUN pip install --no-cache-dir -r /app/requirements-site.txt

COPY . /app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).read()" || exit 1

CMD ["python", "-u", "timeweb_server.py"]
