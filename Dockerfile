FROM python:3.11-slim

# Install system dependencies required for PyMuPDF and Pillow
RUN apt-get update && apt-get install -y \
    libmupdf-dev \
    mupdf-tools \
    libfreetype6-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create the folder structure and give it read/write permissions
RUN mkdir -p generated_pdfs/wp-content/uploads && chmod -R 777 generated_pdfs

EXPOSE 8080

CMD gunicorn app:app --bind 0.0.0.0:$PORT
