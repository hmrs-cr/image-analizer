# Use a slim Python 3.11 image as the base
FROM python:3.11-slim

# Install system dependencies required by OpenCV (which is heavily used by YOLO and DeepFace)
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install all heavy Python dependencies required by both scripts
# We do this before copying scripts to leverage Docker's layer caching
RUN pip install --no-cache-dir \
    ultralytics \
#    deepface \
    imapclient \
    tf-keras \
    requests \
    google-genai

# Copy the application package and entrypoint script into the image
COPY image_analyzer ./image_analyzer
COPY image-analyzer-service.py .
COPY app.js .
COPY style.css .
COPY index.html .
COPY security-cam.md .


# Pre-download and cache YOLO and DeepFace models for instant startup
RUN python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
# RUN python3 -c "from deepface import DeepFace; DeepFace.build_model('ArcFace')"

# Set the default entrypoint
ENTRYPOINT ["python3", "image-analyzer-service.py"]