FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONPATH=/app OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip libglib2.0-0 libgl1 ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip3 install --no-cache-dir torch==2.5.1+cpu torchvision==0.20.1+cpu --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python3", "-m", "factory.dashboard"]
