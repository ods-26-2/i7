"""
Transmite a webcam do computador como vídeo MJPEG por HTTP, para o I7 ler ao vivo:
    python scripts/webcam_mjpeg.py
    (em outro terminal) .\\run.ps1 scripts/aplicacaoModelov5-fast+rodosol.py --video "<endereço mostrado>"

Roda FORA do Docker (no Windows, o Docker não enxerga a webcam). Só precisa de:
    pip install opencv-python

Para não deixar a webcam aberta para qualquer um na rede, o endereço leva um código aleatório
(?t=...), gerado a cada execução. Encerre com Ctrl+C (ou "q" na janela de prévia, com --previa).

Opções:
    --camera 0          índice da webcam (0 = a padrão; tente 1 se houver mais de uma)
    --camera arq.mp4    usa um arquivo de vídeo em laço no lugar da webcam (para testar sem câmera)
    --largura 1280 --altura 720   resolução pedida à webcam (ela usa a mais próxima que suportar)
    --porta 8080
    --previa            abre uma janela com a imagem, para mirar a placa
"""
import argparse
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2


class Captura:
    """Lê a câmera numa thread e guarda só o quadro mais recente, já em JPEG."""

    def __init__(self, fonte, largura, altura, qualidade=85):
        self.arquivo = not fonte.isdigit()
        if self.arquivo:
            self.cap = cv2.VideoCapture(fonte)
        else:
            api = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
            self.cap = cv2.VideoCapture(int(fonte), api)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, largura)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, altura)
        if not self.cap.isOpened():
            raise SystemExit(f"[ERRO] Não foi possível abrir a câmera/vídeo '{fonte}'.")
        self.qualidade = qualidade
        self.fps_arquivo = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.jpeg = None
        self.imagem = None
        self.numero = 0
        self.condicao = threading.Condition()
        self.rodando = True
        threading.Thread(target=self._laco, daemon=True).start()

    def _laco(self):
        while self.rodando:
            ok, img = self.cap.read()
            if not ok:
                if self.arquivo:   # vídeo de teste: volta ao início
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                time.sleep(0.05)
                continue
            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.qualidade])
            with self.condicao:
                self.jpeg, self.imagem = jpg.tobytes(), img
                self.numero += 1
                self.condicao.notify_all()
            if self.arquivo:
                time.sleep(1 / self.fps_arquivo)   # reproduz no ritmo do vídeo, como uma câmera

    def proximo(self, ultimo: int, timeout: float = 2.0):
        """Espera um quadro mais novo que `ultimo`; devolve (numero, jpeg) ou None."""
        with self.condicao:
            self.condicao.wait_for(lambda: self.numero != ultimo, timeout)
            return (self.numero, self.jpeg) if self.numero != ultimo else None

    def parar(self):
        self.rodando = False
        self.cap.release()


def criar_handler(captura: Captura, token: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != "/video" or parse_qs(url.query).get("t", [""])[0] != token:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=quadro")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            ultimo = -1
            try:
                while captura.rodando:
                    novo = captura.proximo(ultimo)
                    if novo is None:
                        continue
                    ultimo, jpeg = novo
                    self.wfile.write(b"--quadro\r\nContent-Type: image/jpeg\r\n"
                                     + f"Content-Length: {len(jpeg)}\r\n\r\n".encode() + jpeg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass   # o leitor desconectou

        def log_message(self, *_):
            pass   # sem log por requisição

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Transmite a webcam como MJPEG por HTTP para o I7.")
    parser.add_argument("--camera", default="0", help="índice da webcam (padrão 0) ou um arquivo de vídeo")
    parser.add_argument("--largura", type=int, default=1280)
    parser.add_argument("--altura", type=int, default=720)
    parser.add_argument("--porta", type=int, default=8080)
    parser.add_argument("--previa", action="store_true", help="mostra a imagem numa janela")
    args = parser.parse_args()

    captura = Captura(args.camera, args.largura, args.altura)
    token = secrets.token_urlsafe(8)
    servidor = ThreadingHTTPServer(("0.0.0.0", args.porta), criar_handler(captura, token))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    while captura.imagem is None:
        time.sleep(0.1)
    altura, largura = captura.imagem.shape[:2]
    print(f"Webcam: {largura}x{altura}. Transmitindo. Ctrl+C para encerrar.")
    print("Endereço para o I7 (no Docker):")
    print(f'  --video "http://host.docker.internal:{args.porta}/video?t={token}"')
    print(f"Para conferir no navegador: http://localhost:{args.porta}/video?t={token}")

    try:
        while True:
            if args.previa and captura.imagem is not None:
                cv2.imshow("webcam (q para sair)", captura.imagem)
                if cv2.waitKey(30) & 0xFF == ord("q"):
                    break
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        servidor.shutdown()
        captura.parar()
        cv2.destroyAllWindows()
        print("Transmissão encerrada.")


if __name__ == "__main__":
    main()