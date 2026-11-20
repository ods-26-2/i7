"""
Leitura da stream de vídeo do P4 (SIS-2) para o I7.

O P4 publica cada quadro numa memória compartilhada LOCAL (GStreamer shmsink), um socket por fonte:
    video  -> /tmp/p4_file_shm     (MP4 reproduzido como câmera; pixels BGR8)
    imx519 -> /tmp/p4_imx519_shm   (câmera física; RAW Bayer RG10, 1920x1080)
    imx477 -> /tmp/p4_imx477_shm   (câmera física; RAW Bayer RG10, 1920x1080)
Cada pacote segue o protocolo P4R1: b"P4R1" + tamanho do cabeçalho (4 bytes, big-endian) + cabeçalho
JSON (source_id, session_id, frame_id, width, height, format, data_size...) + bytes dos pixels.
Referência: https://github.com/ods-26-2/p4 (README e docs/referencia-tecnica.md).

Só funciona na MESMA máquina do P4 (na Jetson). Em Docker, o container precisa enxergar o /tmp e a
memória compartilhada do host (-v /tmp:/tmp --ipc=host).

Precisa do GStreamer com os bindings Python (gi). No Docker do I7 eles vêm instalados; fora dele, no
Debian/Ubuntu: apt install python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-bad

Teste rápido (com o P4 rodando): python scripts/fonte_p4.py video
"""
import json
import os
import stat
import struct
import sys
import time

import cv2
import numpy as np

SOCKETS = {
    "video": "/tmp/p4_file_shm",
    "imx519": "/tmp/p4_imx519_shm",
    "imx477": "/tmp/p4_imx477_shm",
}


# Padrão do mosaico Bayer das câmeras (RG10 = RGGB no V4L2). Atenção: no OpenCV, os nomes das
# conversões Bayer estão "deslocados": RGGB usa COLOR_BayerBG2BGR. (O leitor de exemplo do P4 usa
# COLOR_BayerRG2BGR, que para RGGB troca o vermelho com o azul.) Se as cores saírem trocadas na
# câmera real, troque P4_BAYER no .env.
CONVERSOES_BAYER = {
    "RGGB": cv2.COLOR_BayerBG2BGR,
    "BGGR": cv2.COLOR_BayerRG2BGR,
    "GRBG": cv2.COLOR_BayerGB2BGR,
    "GBRG": cv2.COLOR_BayerGR2BGR,
}
P4_BAYER = os.getenv("P4_BAYER", "RGGB").strip().upper()


def caminho_socket(fonte: str) -> str:
    """'video', 'imx519', 'imx477' ou o caminho do socket."""
    return SOCKETS.get(fonte.strip().lower(), fonte.strip())


def _importar_gstreamer():
    """Importa o GStreamer. Se o Python não achar o `gi` (comum quando o pacote veio do apt e o Python é
    outro), tenta a pasta de pacotes do sistema, como os scripts do próprio P4 fazem."""
    try:
        import gi
    except ImportError:
        sys.path.append("/usr/lib/python3/dist-packages")
        try:
            import gi
        except ImportError:
            raise SystemExit("[ERRO] A leitura do P4 precisa do GStreamer com Python (gi). Fora do Docker: "
                             "apt install python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base "
                             "gstreamer1.0-plugins-bad")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    return Gst


def ler_pacote_p4r1(pacote: bytes) -> tuple[dict, memoryview]:
    """Separa o cabeçalho JSON e os pixels de um pacote P4R1. Levanta ValueError se o pacote for inválido."""
    if len(pacote) < 8 or pacote[:4] != b"P4R1":
        raise ValueError("pacote sem o identificador P4R1")
    tamanho_json = struct.unpack("!I", pacote[4:8])[0]
    inicio = 8 + tamanho_json
    if len(pacote) < inicio:
        raise ValueError("cabeçalho JSON incompleto")
    campos = json.loads(pacote[8:inicio].decode("utf-8"))
    pixels = memoryview(pacote)[inicio:]
    if campos.get("data_size") != len(pixels):
        raise ValueError(f"tamanho dos pixels ({len(pixels)}) diferente do declarado ({campos.get('data_size')})")
    return campos, pixels


def imagem_bgr(campos: dict, pixels) -> np.ndarray:
    """Converte os pixels do pacote numa imagem BGR (formato do OpenCV)."""
    largura, altura, formato = int(campos["width"]), int(campos["height"]), campos["format"]
    if formato == "BGR8":
        return np.frombuffer(pixels, dtype=np.uint8).reshape(altura, largura, 3).copy()
    if formato == "RG10":
        # RAW Bayer de 10 bits em contêiner de 16 bits (little-endian): reduz para 8 bits e faz o
        # demosaicing conforme P4_BAYER (ver CONVERSOES_BAYER).
        if P4_BAYER not in CONVERSOES_BAYER:
            raise ValueError(f"P4_BAYER inválido: {P4_BAYER} (use {', '.join(CONVERSOES_BAYER)})")
        raw16 = np.frombuffer(pixels, dtype="<u2").reshape(altura, largura)
        return cv2.cvtColor((raw16 >> 2).astype(np.uint8), CONVERSOES_BAYER[P4_BAYER])
    raise ValueError(f"formato de pixels não suportado: {formato}")


class LeitorP4:
    """Conecta no socket do P4 e entrega os quadros como (cabeçalho, imagem BGR).

    O appsink guarda só o quadro mais recente (max-buffers=1, drop=true): se o I7 for mais lento que a
    câmera, os quadros intermediários são descartados, sem acumular atraso."""

    def __init__(self, socket: str):
        self.socket = socket
        self._Gst = _importar_gstreamer()
        self._pipeline = None
        self._sink = None
        self.descartados = 0   # pacotes inválidos

    def conectar(self) -> bool:
        if not os.path.exists(self.socket):
            return False
        if not stat.S_ISSOCK(os.stat(self.socket).st_mode):
            raise SystemExit(f"[ERRO] {self.socket} existe, mas não é um socket do P4. Use video, imx519, imx477 "
                             "ou o caminho do socket (ex.: /tmp/p4_file_shm).")
        Gst = self._Gst
        self._pipeline = Gst.parse_launch(
            f"shmsrc socket-path={self.socket} is-live=true do-timestamp=true "
            "! application/x-ods-frame,version=1 "
            "! appsink name=sink sync=false max-buffers=1 drop=true")
        self._sink = self._pipeline.get_by_name("sink")
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.desconectar()
            return False
        return True

    def desconectar(self):
        if self._pipeline is not None:
            self._pipeline.set_state(self._Gst.State.NULL)
        self._pipeline = self._sink = None

    def _erro_no_pipeline(self) -> bool:
        Gst = self._Gst
        mensagem = self._pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        return mensagem is not None

    def ler(self, timeout_s: float = 1.0):
        """Devolve (campos, imagem) ou None se nada chegou no tempo (ou a conexão caiu)."""
        if self._pipeline is None and not self.conectar():
            time.sleep(min(timeout_s, 1.0))
            return None
        Gst = self._Gst
        amostra = self._sink.emit("try-pull-sample", int(timeout_s * Gst.SECOND))
        if amostra is None:
            if self._erro_no_pipeline() or not os.path.exists(self.socket):
                self.desconectar()   # o P4 parou: tenta reconectar na próxima leitura
            return None
        buffer = amostra.get_buffer()
        ok, mapa = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            pacote = bytes(mapa.data)
        finally:
            buffer.unmap(mapa)
        try:
            campos, pixels = ler_pacote_p4r1(pacote)
            return campos, imagem_bgr(campos, pixels)
        except (ValueError, KeyError, json.JSONDecodeError) as erro:
            self.descartados += 1
            if self.descartados <= 3:
                print(f"[P4] pacote descartado: {erro}")
            return None

    def quadros(self, espera_s: float):
        """Gera (campos, imagem) até ficar `espera_s` segundos sem receber nada."""
        ultimo = time.monotonic()
        avisou = False
        while time.monotonic() - ultimo < espera_s:
            resultado = self.ler(1.0)
            if resultado is None:
                if not avisou and self._pipeline is None:
                    print(f"[P4] aguardando o P4 em {self.socket}...")
                    avisou = True
                continue
            ultimo = time.monotonic()
            avisou = False
            yield resultado
        print(f"[P4] {espera_s:g} s sem quadros: encerrando a leitura.")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.desconectar()
        return False


if __name__ == "__main__":
    # Teste de conexão: lê um quadro e salva em /tmp/i7_p4_quadro.png
    fonte = sys.argv[1] if len(sys.argv) > 1 else "video"
    with LeitorP4(caminho_socket(fonte)) as leitor:
        for campos, img in leitor.quadros(10):
            print({k: campos.get(k) for k in ("source_id", "session_id", "frame_id", "width", "height", "format")})
            cv2.imwrite("/tmp/i7_p4_quadro.png", img)
            print("Quadro salvo em /tmp/i7_p4_quadro.png")
            break