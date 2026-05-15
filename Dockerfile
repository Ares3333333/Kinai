FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080

WORKDIR /app

COPY requirements-site.txt ./requirements-site.txt
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements-site.txt

COPY timeweb_server.py ./timeweb_server.py
COPY pitch_site ./pitch_site
COPY docs ./docs
COPY README.md ./README.md

EXPOSE 8080

CMD ["python", "-u", "/app/timeweb_server.py"]
