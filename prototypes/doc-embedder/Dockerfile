FROM python:3.11-slim

WORKDIR /app

ARG GIT_SHA=development
ENV GIT_SHA=${GIT_SHA}

# Install nginx and supervisor
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN sed -i "s/__git_sha__ = \"development\"/__git_sha__ = \"${GIT_SHA}\"/" version.py

# Copy nginx config
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 8080

CMD ["supervisord", "-c", "/app/supervisord.conf"]
