FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATABASE=/data/dashboard.db PORT=5000
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data
EXPOSE 5000
VOLUME /data
# One worker: the background scheduler runs inside the process.
CMD ["sh", "-c", "gunicorn --workers 1 --threads 8 --bind 0.0.0.0:${PORT} 'app:create_app()'"]
