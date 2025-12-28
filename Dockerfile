FROM python:3.11-slim

WORKDIR /app

# (Optional but common) system deps some Python libs may need
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run uses $PORT (usually 8080)
ENV PORT=8080
EXPOSE 8080

# Streamlit must listen on 0.0.0.0 and the Cloud Run port
CMD ["sh", "-c", "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true"]
