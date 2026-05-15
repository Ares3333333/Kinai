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

CMD ["sh", "-c", "echo Starting Kinaesthetic AI on HOST=$HOST PORT=$PORT && python timeweb_server.py"]
