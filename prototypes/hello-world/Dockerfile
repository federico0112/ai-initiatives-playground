FROM python:3.11-slim

WORKDIR /app

ARG GIT_SHA=development
ENV GIT_SHA=${GIT_SHA}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Inject git SHA into version.py at build time
RUN sed -i "s/__git_sha__ = \"development\"/__git_sha__ = \"${GIT_SHA}\"/" version.py

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
