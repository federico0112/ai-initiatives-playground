FROM python:3.11-slim

WORKDIR /app

ARG GIT_SHA=development
ENV GIT_SHA=${GIT_SHA}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The simulated data is not committed; generate it into the image (deterministic, about a minute)
RUN python dataset/generate_dataset.py > /dev/null && python dataset/verify_dataset.py > /dev/null

# Inject git SHA into version.py at build time
RUN sed -i "s/__git_sha__ = \"development\"/__git_sha__ = \"${GIT_SHA}\"/" version.py

EXPOSE 8080

# One worker keeps the in-memory usage roll-up shared; scenario runs take a few seconds
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "300", "app:app"]
