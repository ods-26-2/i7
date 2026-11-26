"""
Leitura de vídeo AO VIVO por endereço (ex.: a webcam transmitida pelo webcam_mjpeg.py, ou uma câmera IP
com http:// ou rtsp://). Uma thread lê sem parar e guarda só o quadro mais recente: se o I7 for mais
lento que a câmera, os quadros intermediários são descartados, sem acumular atraso (como na stream do P4).
"""
import threading
import time

import cv2


def eh_endereco(texto: str) -> bool:
    return texto.strip().lower().startswith(("http://", "https://", "rtsp://", "rtmp://"))


class LeitorAoVivo:
    def __init__(self, endereco: str):
        self.endereco = endereco
        self.cap = None
        self.imagem = None
        self.numero = -1          # contador de quadros recebidos (vira o frame_index)
        self.condicao = threading.Condition()
        self.rodando = True
        self._thread = threading.Thread(target=self._laco, daemon=True)
        self._thread.start()

    def _laco(self):
        while self.rodando:
            if self.cap is None:
                self.cap = cv2.VideoCapture(self.endereco)
                if not self.cap.isOpened():
                    self.cap = None
                    time.sleep(1)   # transmissão ainda não começou: tenta de novo
                    continue
            ok, img = self.cap.read()
            if not ok:
                self.cap.release()
                self.cap = None     # a transmissão caiu: reconecta
                time.sleep(0.5)
                continue
            with self.condicao:
                self.imagem = img
                self.numero += 1
                self.condicao.notify_all()

    def quadros(self, espera_s: float):
        """Gera (numero, imagem) sempre com o quadro mais recente, até ficar `espera_s` s sem quadros."""
        ultimo = -1
        visto = time.monotonic()
        avisou = False
        while time.monotonic() - visto < espera_s:
            with self.condicao:
                self.condicao.wait_for(lambda: self.numero != ultimo, 1.0)
                if self.numero == ultimo:
                    novo = None
                else:
                    novo = (self.numero, self.imagem)
            if novo is None:
                if not avisou and self.numero < 0:
                    print(f"[AO VIVO] aguardando a transmissão em {self.endereco.split('?')[0]}...")
                    avisou = True
                continue
            ultimo, img = novo
            visto = time.monotonic()
            yield novo
        print(f"[AO VIVO] {espera_s:g} s sem quadros: encerrando a leitura.")

    def fechar(self):
        self.rodando = False
        self._thread.join(timeout=2)
        if self.cap is not None:
            self.cap.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()
        return False