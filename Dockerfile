FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Bibliotecas de sistema que o OpenCV precisa
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyTorch versão CPU (bem menor que a versão com CUDA)
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Dependências primeiro: o Docker reaproveita essa camada se só o código mudar
COPY requirements.txt .
RUN pip install -r requirements.txt

# Código e modelos
COPY modelos/ modelos/
COPY scripts/ scripts/

ENTRYPOINT ["python"]
CMD ["scripts/aplicacaoModelov5-fast+rodosol.py", "-n", "20"]