FROM python:3.11-slim

WORKDIR /app

# Cài đặt thư viện hệ thống tối thiểu
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Cài đặt Python server dependencies
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Copy mã nguồn
COPY . .

# Render tự cấp biến $PORT khi khởi chạy
ENV PORT=8000
EXPOSE $PORT

CMD ["sh", "-c", "uvicorn server.gateway:app --host 0.0.0.0 --port $PORT"]
