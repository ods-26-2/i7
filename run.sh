#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

IMAGEM=parkflow-i7

# ./run.sh build  -> reconstrói a imagem
if [ "$1" = "build" ]; then
  docker build -t "$IMAGEM" .
  exit 0
fi

if [ ! -f .env ]; then
  echo "Falta o arquivo .env (copie o .env.example para .env)"
  exit 1
fi

mkdir -p saidas datasetConvertidoYOLO

docker run --rm --env-file .env \
  -v "$(pwd)/datasetConvertidoYOLO:/app/datasetConvertidoYOLO" \
  -v "$(pwd)/saidas:/app/saidas" \
  -v i7-ocr-cache:/root/.cache/fast-plate-ocr \
  "$IMAGEM" "$@"