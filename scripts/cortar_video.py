"""
Corta um trecho de um vídeo e grava um MP4 novo (só a imagem, sem áudio), sem precisar do ffmpeg.
Útil para preparar vídeos curtos de teste, por exemplo para a "câmera simulada" do P4.

Uso:
    python scripts/cortar_video.py videos/parkingcam.mp4 --inicio 2:27 --fim 3:03
    python scripts/cortar_video.py videos/parkingcam.mp4 --inicio 147 --fim 183 --saida videos/portao.mp4

Sem --saida, grava ao lado do original: <nome>_<inicio>-<fim>s.mp4.
"""
import argparse
import math
from pathlib import Path

import cv2

RAIZ = Path(__file__).resolve().parent.parent


def segundos(texto: str) -> float:
    """Aceita segundos (90 ou 90.5) ou minutos:segundos (1:30)."""
    try:
        if ":" in texto:
            minutos, seg = texto.split(":", 1)
            valor = int(minutos) * 60 + float(seg)
        else:
            valor = float(texto)
    except ValueError:
        valor = -1
    if valor < 0:
        raise argparse.ArgumentTypeError(f"'{texto}' não é um tempo válido (use 90, 90.5 ou 1:30)")
    return valor


def main() -> int:
    parser = argparse.ArgumentParser(description="Corta um trecho de um vídeo (sem áudio).")
    parser.add_argument("video", help="vídeo de origem (relativo à raiz do projeto)")
    parser.add_argument("--inicio", type=segundos, default=0.0, help="início do trecho (padrão: 0)")
    parser.add_argument("--fim", type=segundos, default=None, help="fim do trecho (padrão: fim do vídeo)")
    parser.add_argument("--saida", help="arquivo de saída (padrão: <nome>_<inicio>-<fim>s.mp4)")
    args = parser.parse_args()

    origem = RAIZ / args.video
    video = cv2.VideoCapture(str(origem))
    if not video.isOpened():
        print(f"[ERRO] Não foi possível abrir {origem}")
        return 1
    fps = video.get(cv2.CAP_PROP_FPS) or 30.0
    largura = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    altura = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if args.fim is not None and args.fim <= args.inicio:
        print("[ERRO] --fim deve ser maior que --inicio.")
        return 1

    fim_txt = f"{args.fim:g}" if args.fim is not None else "fim"
    destino = RAIZ / args.saida if args.saida else origem.with_name(f"{origem.stem}_{args.inicio:g}-{fim_txt}s.mp4")
    gravador = cv2.VideoWriter(str(destino), cv2.VideoWriter_fourcc(*"mp4v"), fps, (largura, altura))

    primeiro = round(args.inicio * fps)
    ultimo = math.floor(args.fim * fps) if args.fim is not None else None
    video.set(cv2.CAP_PROP_POS_FRAMES, primeiro)
    gravados = 0
    numero = primeiro
    while ultimo is None or numero <= ultimo:
        ok, img = video.read()
        if not ok:
            break
        gravador.write(img)
        gravados += 1
        numero += 1
    video.release()
    gravador.release()

    if not gravados:
        print("[ERRO] Nenhum quadro no trecho pedido.")
        destino.unlink(missing_ok=True)
        return 1
    print(f"Trecho salvo em: {destino.relative_to(RAIZ) if destino.is_relative_to(RAIZ) else destino}")
    print(f"{gravados} quadros, {largura}x{altura}, {fps:.1f} q/s ({gravados / fps:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())