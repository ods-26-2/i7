"""
Pipeline: detecção de placa (YOLO 4 classes) -> recorte -> OCR (EasyOCR ou PaddleOCR)
-> validação por formato (Mercosul / antiga) -> mosaico + CSV.

Mosaico: [imagem com detecção | recorte(s) + texto + tipo da placa]

Dependências:
    pip install ultralytics opencv-python numpy
    pip install easyocr                      # opção 1
    pip install paddlepaddle paddleocr       # opção 2

Teclas na janela:  q / ESC = sair | qualquer outra = próxima imagem
"""
import csv
import re
from pathlib import Path

import cv2
import numpy as np

# =========================== VARIÁVEIS GLOBAIS ===========================
PASTA_IMAGENS = "/home/gabriel/Desktop/ODS/ImgsTesteModelo"          # pasta com as imagens de teste
MODELO_PLACA = "/home/gabriel/Desktop/ODS/modelos/modelo4classesV1/best.pt"   # seu detector de placas
MOTOR_OCR = "easyocr"                    # "easyocr" ou "paddle"
PASTA_SAIDA = "/home/gabriel/Desktop/ODS/saida_mosaicos/saida2classesV1"          # mosaicos + resultados.csv
          # mosaicos + resultados.csv

CONF_DETECCAO = 0.25                     # confiança mínima do detector
MOSTRAR_JANELA = True                    # False = só salva (útil em servidor/headless)
USAR_GPU = True
ALTURA_MOSAICO = 640                     # altura da imagem principal no mosaico
LARGURA_PAINEL = 420                     # largura do painel do recorte
ALTURA_PAINEL = 300                      # altura máxima do painel do recorte
ALTURA_MAX_RECORTE = 220                 # altura máxima do recorte no painel
FAIXA_LINHA_PX = 20                      # tolerância em y para considerar "mesma linha" (EasyOCR)
EXTENSOES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Mapeamento para exibição amigável das 4 classes no mosaico
NOMES_CLASSES = {
    "placa_1L_BR": "Antiga (1 Linha)",
    "placa_2L_BR": "Antiga (2 Linhas)",
    "placa_1L_ME": "Mercosul (1 Linha)",
    "placa_2L_ME": "Mercosul (2 Linhas)",
}

# Tipos de placa reconhecidos pelo formato do texto (nome, regex)
PADROES_PLACA = [
    ("Mercosul", re.compile(r"[A-Z]{3}[0-9][A-Z][0-9]{2}")),  # ABC1D23
    ("Antiga",   re.compile(r"[A-Z]{3}[0-9]{4}")),            # ABC1234
]
TIPO_DESCONHECIDO = "Desconhecido"
# =========================================================================


# ------------------------ VALIDAÇÃO / TIPO DA PLACA ---------------------
def escolher_placa(textos, confs):
    """
    Recebe as linhas lidas pelo OCR e devolve (placa, confiança, tipo).
    Ignora ruídos como a palavra BRASIL, mesmo que grudada na placa.
    """
    if not textos:
        return "", 0.0, TIPO_DESCONHECIDO

    # limpa: maiúsculas, sem espaço, hífen ou qualquer símbolo
    limpos = [re.sub(r"[^A-Z0-9]", "", t.upper()) for t in textos]

    # candidatos: cada linha isolada + todas juntas (placas de moto em 2 linhas)
    candidatos = list(zip(limpos, confs))
    candidatos.append(("".join(limpos), float(np.mean(confs))))

    for texto, conf in candidatos:
        for tipo, padrao in PADROES_PLACA:
            m = padrao.search(texto)
            if m:
                return m.group(), float(conf), tipo

    # nada casou com o formato: devolve a melhor linha que não seja "BRASIL"
    validos = [(t, c) for t, c in zip(limpos, confs) if t and "BRASIL" not in t]
    if validos:
        t, c = max(validos, key=lambda x: x[1])
        return t, float(c), TIPO_DESCONHECIDO
    return "", 0.0, TIPO_DESCONHECIDO


# ------------------------------- OCR -----------------------------------
class OCR:
    """Wrapper para trocar de motor sem mexer no resto do código."""

    def __init__(self, motor: str, gpu: bool = True):
        self.motor = motor.lower()
        if self.motor == "easyocr":
            import easyocr
            self.reader = easyocr.Reader(["en"], gpu=gpu)
        elif self.motor == "paddle":
            from paddleocr import PaddleOCR
            try:  # PaddleOCR 3.x
                self.reader = PaddleOCR(
                    lang="en",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    enable_mkldnn=False,  # evita bug oneDNN/PIR do Paddle 3.x na CPU
                )
                self.v3 = True
            except (TypeError, ValueError):  # PaddleOCR 2.x
                self.reader = PaddleOCR(lang="en", use_angle_cls=False, use_gpu=gpu, show_log=False)
                self.v3 = False
        else:
            raise ValueError("MOTOR_OCR deve ser 'easyocr' ou 'paddle'")

    def ler(self, crop_bgr: np.ndarray) -> tuple[str, float, str]:
        """Retorna (placa, confiança, tipo)."""
        if self.motor == "easyocr":
            res = self.reader.readtext(
                crop_bgr, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
            )
            # de cima p/ baixo (em faixas de y) e, dentro da faixa, da esquerda p/ direita
            res = sorted(res, key=lambda r: (round(r[0][0][1] / FAIXA_LINHA_PX), r[0][0][0]))
            textos = [r[1] for r in res]
            confs = [r[2] for r in res]
            return escolher_placa(textos, confs)

        # paddle
        if self.v3:
            out = self.reader.predict(crop_bgr)
            if not out:
                return "", 0.0, TIPO_DESCONHECIDO
            textos = out[0].get("rec_texts", [])
            confs = out[0].get("rec_scores", [])
        else:
            out = self.reader.ocr(crop_bgr, cls=False)
            linhas = out[0] if out and out[0] else []
            textos = [l[1][0] for l in linhas]
            confs = [l[1][1] for l in linhas]
        return escolher_placa(textos, confs)


# ---------------------------- DETECÇÃO ---------------------------------
def detectar_placas(modelo, img_bgr):
    """Retorna lista de (x1, y1, x2, y2, conf, nome_classe)."""
    res = modelo.predict(img_bgr, conf=CONF_DETECCAO, verbose=False)[0]
    caixas = []
    for b in res.boxes:
        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
        cls_id = int(b.cls[0])
        nome_classe = res.names[cls_id]
        caixas.append((x1, y1, x2, y2, float(b.conf[0]), nome_classe))
    return caixas


# ----------------------------- MOSAICO ---------------------------------
def redimensionar_largura(img, largura):
    h, w = img.shape[:2]
    return cv2.resize(img, (largura, max(1, int(h * largura / w))))


def montar_painel(crops, resultados, caixas):
    """Coluna da direita: cada recorte com placa, confianças e classe do modelo logo abaixo."""
    blocos = []
    for crop, (texto, conf_ocr, tipo_regex), (_, _, _, _, conf_det, nome_classe) in zip(crops, resultados, caixas):
        c = redimensionar_largura(crop, LARGURA_PAINEL)
        c = c[:ALTURA_MAX_RECORTE]
        barra = np.full((120, LARGURA_PAINEL, 3), 30, np.uint8)
        
        classe_formatada = NOMES_CLASSES.get(nome_classe, nome_classe)

        cv2.putText(barra, texto if texto else "(sem leitura)", (10, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(barra, f"det {conf_det:.2f} | ocr {conf_ocr:.2f}", (10, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(barra, f"modelo: {classe_formatada} | regex: {tipo_regex}", (10, 106),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1, cv2.LINE_AA)
        blocos.append(np.vstack([c, barra]))

    if not blocos:
        vazio = np.full((ALTURA_MOSAICO, LARGURA_PAINEL, 3), 30, np.uint8)
        cv2.putText(vazio, "nenhuma placa detectada", (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
        return vazio
    return np.vstack(blocos)


def juntar(anot, painel):
    """Une imagem anotada e painel, completando a altura com margem cinza."""
    h = max(anot.shape[0], painel.shape[0])

    def pad(im):
        return cv2.copyMakeBorder(im, 0, h - im.shape[0], 0, 0,
                                  cv2.BORDER_CONSTANT, value=(30, 30, 30))

    return np.hstack([pad(anot), pad(painel)])


def desenhar_deteccoes(img, caixas):
    anot = img.copy()
    for (x1, y1, x2, y2, _, nome_classe) in caixas:
        cv2.rectangle(anot, (x1, y1), (x2, y2), (0, 255, 0), 3)
        classe_formatada = NOMES_CLASSES.get(nome_classe, nome_classe)
        cv2.putText(anot, classe_formatada, (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    h, w = anot.shape[:2]
    return cv2.resize(anot, (int(w * ALTURA_MOSAICO / h), ALTURA_MOSAICO))


# ------------------------------- MAIN ----------------------------------
def main():
    from ultralytics import YOLO

    pasta = Path(PASTA_IMAGENS)
    saida = Path(PASTA_SAIDA)
    saida.mkdir(parents=True, exist_ok=True)

    imagens = sorted(p for p in pasta.rglob("*") if p.suffix.lower() in EXTENSOES)
    print(f"{len(imagens)} imagens encontradas | OCR: {MOTOR_OCR}")

    detector = YOLO(MODELO_PLACA)
    ocr = OCR(MOTOR_OCR, gpu=USAR_GPU)

    with open(saida / "resultados.csv", "w", newline="", encoding="utf-8") as arq_csv:
        escritor = csv.writer(arq_csv)
        escritor.writerow(["imagem", "placa", "tipo_regex", "classe_modelo", "conf_det", "conf_ocr"])

        for i, caminho in enumerate(imagens, 1):
            img = cv2.imread(str(caminho))
            if img is None:
                print(f"[!] não consegui abrir {caminho}")
                continue

            caixas, crops, resultados = [], [], []
            for caixa in detectar_placas(detector, img):
                x1, y1, x2, y2, _, _ = caixa
                crop = img[max(y1, 0):y2, max(x1, 0):x2]
                if crop.size == 0:
                    continue
                caixas.append(caixa)
                crops.append(crop)
                resultados.append(ocr.ler(crop))

            mosaico = juntar(desenhar_deteccoes(img, caixas),
                             montar_painel(crops, resultados, caixas))
            cv2.imwrite(str(saida / f"{caminho.stem}_mosaico.jpg"), mosaico)

            for (placa, conf_ocr, tipo_regex), (_, _, _, _, conf_det, nome_classe) in zip(resultados, caixas):
                escritor.writerow([caminho.name, placa, tipo_regex, NOMES_CLASSES.get(nome_classe, nome_classe), f"{conf_det:.3f}", f"{conf_ocr:.3f}"])

            print(f"[{i}/{len(imagens)}] {caminho.name} -> {[(r[0], r[2]) for r in resultados]}")

            if MOSTRAR_JANELA:
                cv2.imshow("Deteccao de placa + OCR", mosaico)
                if cv2.waitKey(0) & 0xFF in (ord("q"), 27):
                    break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()