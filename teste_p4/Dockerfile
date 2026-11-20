# P4 (SIS-2) para TESTE LOCAL do I7, sem a Jetson: roda a "câmera simulada" do P4 (um MP4 reproduzido
# como câmera) e publica os quadros na memória compartilhada, como na Jetson. Usa o código do repositório
# do P4 sem alterá-lo. Não serve para as câmeras físicas.
#
#   docker build -t p4-teste teste_p4
#   (comandos completos no README, seção "Stream do P4")
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git libgl1 libglib2.0-0 \
        python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-bad \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/ods-26-2/p4.git /p4 \
    && pip install -r /p4/requirements.txt

WORKDIR /p4
# O gi vem do apt (Python 3.11 do Debian, a mesma versão desta imagem), como nos scripts do próprio P4.
ENV PYTHONPATH=/p4:/usr/lib/python3/dist-packages \
    P4_SOURCE=file \
    P4_VIDEO_PATH=/videos/video.mp4 \
    P4_ATIVAR_KAFKA=false \
    P4_MODO_DIAGNOSTICO=false

CMD ["python", "-m", "src.main"]