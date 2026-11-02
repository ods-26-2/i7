"""
Pipeline ALPR Atualizado:
1. Detecção YOLO com 4 classes (placa_1L_BR, placa_2L_BR, placa_1L_ME, placa_2L_ME)
2. OCR (EasyOCR / PaddleOCR)
3. Correção Posicional de Caracteres Confusos (O->0, I->1, etc.) com base no padrão esperado
4. Mosaico visual + Exportação CSV
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
PASTA_SAIDA = "/home/gabriel/Desktop/ODS/saida_mosaicos/saida4classesV1"          # mosaicos + resultados.csv

CONF_DETECCAO = 0.25                     # confiança mínima do detector
MOSTRAR_JANELA = True                    # False = só salva
USAR_GPU = True
ALTURA_MOSAICO = 640                     # altura da imagem principal
LARGURA_PAINEL = 420                     # largura do painel do recorte
ALTURA_MAX_RECORTE = 420                 # altura máxima do recorte no painel
FAIXA_LINHA_PX = 20                      # tolerância em y para linhas do EasyOCR
EXTENSOES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Mapeamento de nomes amigáveis para exibição
NOMES_CLASSES = {
    "placa_1L_BR": "Antiga (1 Linha)",
    "placa_2L_BR": "Antiga (2 Linhas)",
    "placa_1L_ME": "Mercosul (1 Linha)",
    "placa_2L_ME": "Mercosul (2 Linhas)",
}

# Dicionários de Substituição para Correção OCR
NUMERO_PARA_LETRA = {'0': 'O', '1': 'I', '2': 'Z', '3': 'B', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B'}
LETRA_PARA_NUMERO = {'O': '0', 'Q': '0', 'D': '0', 'I': '1', 'L': '1', 'Z': '2', 'B': '8', 'S': '5', 'G': '6', 'T': '7', 'A': '4'}

# Regex de confirmação pós-correção
REGEX_MERCOSUL = re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$")
REGEX_ANTIGA   = re.compile(r"^[A-Z]{3}[0-9]{4}$")
# =========================================================================


def corrigir_caracteres(texto: str, padrao_esperado: str) -> str:
    """
    Substitui caracteres mal interpretados pela posição exata na placa.
    padrao_esperado: 'LLLNLNN' (Mercosul) ou 'LLLNNNN' (Antiga)
    """
    if len(texto) != 7:
        return texto  # Se não capturou exatamente 7 caracteres, retorna como está para análise

    resultado = []
    for char, tipo in zip(texto, padrao_esperado):
        if tipo == 'L':  # Espera uma Letra
            if char in NUMERO_PARA_LETRA:
                resultado.append(NUMERO_PARA_LETRA[char])
            else:
                resultado.append(char)
        elif tipo == 'N':  # Espera um Número
            if char in LETRA_PARA_NUMERO:
                resultado.append(LETRA_PARA_NUMERO[char])
            else:
                resultado.append(char)
        else:
            resultado.append(char)

    return "".join(resultado)


def processar_leitura(textos, confs, classe_detectada):
    """
    Higieniza, corrige caracteres confusos conforme o modelo predito e valida o regex.
    """
    if not textos:
        return "", 0.0, "Sem Leitura"

    # Limpa caracteres especiais, espaços e converte para caixa alta
    limpos = [re.sub(r"[^A-Z0-9]", "", t.upper()) for t in textos]
    texto_bruto = "".join([t for t in limpos if "BRASIL" not in t])
    conf_media = float(np.mean(confs)) if confs else 0.0

    # Determina o tipo pelo padrão da classe detectada pelo YOLO
    eh_mercosul = "ME" in classe_detectada
    padrao = "LLLNLNN" if eh_mercosul else "LLLNNNN"
    regex = REGEX_MERCOSUL if eh_mercosul else REGEX_ANTIGA

    # Aplica a correção de letras/números
    texto_corrigido = corrigir_caracteres(texto_bruto, padrao)

    # Validação via Regex
    if regex.match(texto_corrigido):
        return texto_corrigido, conf_media, NOMES_CLASSES.get(classe_detectada, classe_detectada)

    # Tenta validar no formato oposto caso o detector tenha errado a classe (Fallback)
    padrao_alt = "LLLNNNN" if eh_mercosul else "LLLNLNN"
    regex_alt = REGEX_ANTIGA if eh_mercosul else REGEX_MERCOSUL
    texto_alt = corrigir_caracteres(texto_bruto, padrao_alt)

    if regex_alt.match(texto_alt):
        tipo_alt = "Antiga (Fallback)" if eh_mercosul else "Mercosul (Fallback)"
        return texto_alt, conf_media, tipo_alt

    # Se não atendeu a nenhum regex perfeitamente
    return texto_corrigido if texto_corrigido else texto_bruto, conf_media, f"{NOMES_CLASSES.get(classe_detectada, classe_detectada)} (Inválida)"


class OCR:
    def __init__(self, motor: str, gpu: bool = True):
        self.motor = motor.lower()
        if self.motor == "easyocr":
            import easyocr
            self.reader = easyocr.Reader(["en"], gpu=gpu)
        elif self.motor == "paddle":
            from paddleocr import PaddleOCR
            try:
                self.reader = PaddleOCR(lang="en", use_doc_orientation_classify=False, enable_mkldnn=False)
                self.v3 = True
            except (TypeError, ValueError):
                self.reader = PaddleOCR(lang="en", use_angle_cls=False, use_gpu=gpu, show_log=False)
                self.v3 = False

    def ler(self, crop_bgr: np.ndarray, classe_detectada: str) -> tuple[str, float, str]:
        if self.motor == "easyocr":
            res = self.reader.readtext(
                crop_bgr, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            )
            res = sorted(res, key=lambda r: (round(r[0][0][1] / FAIXA_LINHA_PX), r[0][0][0]))
            textos = [r[1] for r in res]
            confs = [r[2] for r in res]
            return processar_leitura(textos, confs, classe_detectada)

        # paddle
        if self.v3:
            out = self.reader.predict(crop_bgr)
            if not out:
                return "", 0.0, "Sem Leitura"
            textos = out[0].get("rec_texts", [])
            confs = out[0].get("rec_scores", [])
        else:
            out = self.reader.ocr(crop_bgr, cls=False)
            linhas = out[0] if out and out[0] else []
            textos = [l[1][0] for l in linhas]
            confs = [l[1][1] for l in linhas]

        return processar_leitura(textos, confs, classe_detectada)


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


def redimensionar_largura(img, largura):
    h, w = img.shape[:2]
    return cv2.resize(img, (largura, max(1, int(h * largura / w))))


def montar_painel(crops, resultados, caixas):
    blocos = []
    for crop, (texto, conf_ocr, tipo_exibicao), (_, _, _, _, conf_det, _) in zip(crops, resultados, caixas):
        c = redimensionar_largura(crop, LARGURA_PAINEL)
        c = c[:ALTURA_MAX_RECORTE]
        barra = np.full((120, LARGURA_PAINEL, 3), 30, np.uint8)
        
        # Define a cor do texto no mosaico (Verde para válido, Amarelo para inválido)
        cor_texto = (0, 255, 0) if "Inválida" not in tipo_exibicao and "Sem Leitura" not in tipo_exibicao else (0, 255, 255)

        cv2.putText(barra, texto if texto else "(sem leitura)", (10, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, cor_texto, 2, cv2.LINE_AA)
        cv2.putText(barra, f"det {conf_det:.2f} | ocr {conf_ocr:.2f}", (10, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(barra, f"Classe: {tipo_exibicao}", (10, 106),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 1, cv2.LINE_AA)
        blocos.append(np.vstack([c, barra]))

    if not blocos:
        vazio = np.full((ALTURA_MOSAICO, LARGURA_PAINEL, 3), 30, np.uint8)
        cv2.putText(vazio, "nenhuma placa detectada", (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
        return vazio
    return np.vstack(blocos)


def juntar(anot, painel):
    h = max(anot.shape[0], painel.shape[0])

    def pad(im):
        return cv2.copyMakeBorder(im, 0, h - im.shape[0], 0, 0,
                                  cv2.BORDER_CONSTANT, value=(30, 30, 30))

    return np.hstack([pad(anot), pad(painel)])


def desenhar_deteccoes(img, caixas):
    anot = img.copy()
    for (x1, y1, x2, y2, _, nome_classe) in caixas:
        cv2.rectangle(anot, (x1, y1), (x2, y2), (0, 255, 0), 3)
        cv2.putText(anot, NOMES_CLASSES.get(nome_classe, nome_classe), (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    h, w = anot.shape[:2]
    return cv2.resize(anot, (int(w * ALTURA_MOSAICO / h), ALTURA_MOSAICO))


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
        escritor.writerow(["imagem", "placa_lida", "tipo_detectado", "conf_det", "conf_ocr"])

        for i, caminho in enumerate(imagens, 1):
            img = cv2.imread(str(caminho))
            if img is None:
                continue

            caixas, crops, resultados = [], [], []
            for caixa in detectar_placas(detector, img):
                x1, y1, x2, y2, _, nome_classe = caixa
                crop = img[max(y1, 0):y2, max(x1, 0):x2]
                if crop.size == 0:
                    continue
                caixas.append(caixa)
                crops.append(crop)
                
                # Executa o OCR passando a classe identificada pelo modelo
                resultados.append(ocr.ler(crop, nome_classe))

            mosaico = juntar(desenhar_deteccoes(img, caixas),
                             montar_painel(crops, resultados, caixas))
            cv2.imwrite(str(saida / f"{caminho.stem}_mosaico.jpg"), mosaico)

            for (placa, conf_ocr, tipo), (_, _, _, _, conf_det, classe_orig) in zip(resultados, caixas):
                escritor.writerow([caminho.name, placa, tipo, f"{conf_det:.3f}", f"{conf_ocr:.3f}"])

            print(f"[{i}/{len(imagens)}] {caminho.name} -> {[(r[0], r[2]) for r in resultados]}")

            if MOSTRAR_JANELA:
                cv2.imshow("Deteccao de placa + OCR", mosaico)
                if cv2.waitKey(0) & 0xFF in (ord("q"), 27):
                    break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()