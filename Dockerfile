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

# Biblioteca P2 (SIS-1), versão 0.1.0. Instalada com --no-deps porque ela declara numpy<2, mas o
# restante do projeto usa numpy 2; na CPU o P2 funciona com os dois. onnx é a dependência do backend de CPU.
RUN pip install --no-deps https://github.com/ods-26-2/p2/releases/download/v0.1.0/p2-0.1.0-py3-none-any.whl \
    && pip install "typing_extensions>=4.10" "onnx>=1.16"

# Modelos. O pacote do B5 (modelos/app-plates-1.0.0) vira um engine do P2; o p2-build confere o hash do ONNX.
COPY modelos/ modelos/
ENV P2_ENGINE_DIR=/opt/ods/engines
RUN p2-build modelos/app-plates-1.0.0 --cpu-only

# Código (por último, para mudar um script não refazer as camadas acima)
COPY scripts/ scripts/

ENTRYPOINT ["python"]
CMD ["scripts/aplicacaoModelov5-fast+rodosol.py", "-n", "20"]