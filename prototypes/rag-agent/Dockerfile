FROM python:3.11-slim

WORKDIR /app

ARG GIT_SHA=development
ENV GIT_SHA=${GIT_SHA}

# Install system dependencies for document processing (Docling/pypdfium2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libxcb1 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Inject git SHA at build time
RUN sed -i "s/__git_sha__ = \"development\"/__git_sha__ = \"${GIT_SHA}\"/" version.py

EXPOSE 8080

# Run uvicorn directly
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
