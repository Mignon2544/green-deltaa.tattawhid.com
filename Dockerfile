FROM python:3.11-slim

# Install system dependencies required for PyMuPDF and Pillow
RUN apt-get update && apt-get install -y \
    libmupdf-dev \
    mupdf-tools \
    libfreetype6-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create the folder structure and give it read/write permissions
RUN mkdir -p generated_pdfs/wp-content/uploads && chmod -R 777 generated_pdfs

# Expose the port
EXPOSE 8080

# Start the application
CMD gunicorn app:app --bind 0.0.0.0:$PORT
