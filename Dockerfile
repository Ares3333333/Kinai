FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080

WORKDIR /app

COPY timeweb_server.py /app/timeweb_server.py
COPY pitch_site /app/pitch_site
COPY docs /app/docs
COPY README.md /app/README.md

EXPOSE 8080

ENTRYPOINT ["python", "-u", "/app/timeweb_server.py"]
