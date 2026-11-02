"""
Pipeline ALPR Completo (Modo Headless/Apenas Salvar):
1. Detecção YOLO (4 classes)
2. Pré-processamento de imagem (Resize + CLAHE + Padding)
3. OCR + Limpeza estrita para forçar exatamente 7 caracteres
4. Correção posicional obrigatória por dicionários (Letra -> Número / Número -> Letra)
5. Validação por Regex + Geração de Mosaico e CSV
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
MOSTRAR_JANELA = False                   # False = Apenas salva (sem janelas na tela)
USAR_GPU = True
ALTURA_MOSAICO = 640                     # altura da imagem principal no mosaico
LARGURA_PAINEL = 420                     # largura do painel do recorte
ALTURA_MAX_RECORTE = 300                 # altura máxima do recorte no painel
FAIXA_LINHA_PX = 20                      # tolerância em y para considerar "mesma linha"
EXTENSOES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

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


# ---------------------- PRÉ-PROCESSAMENTO DE IMAGEM ----------------------
def pre_processar_placa(crop_bgr):
    """Aplica super-resolução, contraste adaptativo e margem neutra."""
    h, w = crop_bgr.shape[:2]

    if h < 60 or w < 180:
        crop_bgr = cv2.resize(crop_bgr, (int(w * 2.5), int(h * 2.5)), interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_clahe = clahe.apply(gray)

    gray_padded = cv2.copyMakeBorder(
        gray_clahe, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=128
    )

    return cv2.cvtColor(gray_padded, cv2.COLOR_GRAY2BGR)


# ------------------ CORREÇÃO E VALIDAÇÃO DE CARACTERES -------------------
def forcar_sete_caracteres(texto: str) -> str:
    """
    Remove ruídos comuns no meio da placa (como pontos ou traços lidos como caracteres)
    para garantir uma string de exatamente 7 caracteres.
    """
    if len(texto) == 7:
        return texto

    # Se leu mais de 7 (ex: OD149001 - onde o 4 foi o ponto central)
    if len(texto) > 7:
        # Se o caractere da 4ª posição (índice 3) for um ruído entre letras e números, remove ele
        # Exemplo: OD1 + 4 + 9001 -> remove o '4' intermediário
        candidato_sem_meio = texto[:3] + texto[4:]
        if len(candidato_sem_meio) == 7:
            return candidato_sem_meio
        # Caso contrário, pega os primeiros 7 caracteres
        return texto[:7]

    return texto


def aplicar_mascara_posicional(texto: str, padrao_esperado: str) -> str:
    """
    Força caractere a caractere a conversão por dicionário com base na posição da máscara.
    padrao_esperado: 'LLLNLNN' (Mercosul) ou 'LLLNNNN' (Antiga)
    """
    texto_ajustado = forcar_sete_caracteres(texto)
    
    if len(texto_ajustado) != 7:
        return texto_ajustado

    resultado = []
    for char, tipo in zip(texto_ajustado, padrao_esperado):
        if tipo == 'L':  # Posição de LETRA
            resultado.append(NUMERO_PARA_LETRA.get(char, char))
        elif tipo == 'N':  # Posição de NÚMERO
            resultado.append(LETRA_PARA_NUMERO.get(char, char))
        else:
            resultado.append(char)

    return "".join(resultado)


def processar_leitura(textos, confs, classe_detectada):
    """
    Higieniza, força 7 caracteres, aplica substituição por dicionário e valida no Regex.
    """
    if not textos:
        return "", 0.0, "Sem Leitura"

    # Limpeza básica (remover espaços, hífens, pontuações e a palavra BRASIL)
    limpos = [re.sub(r"[^A-Z0-9]", "", t.upper()) for t in textos]
    texto_bruto = "".join([t for t in limpos if "BRASIL" not in t])
    conf_media = float(np.mean(confs)) if confs else 0.0

    # Define o padrão da classe detectada pelo YOLO
    eh_mercosul = "ME" in classe_detectada
    padrao_primario = "LLLNLNN" if eh_mercosul else "LLLNNNN"
    regex_primario  = REGEX_MERCOSUL if eh_mercosul else REGEX_ANTIGA

    # 1. Tenta forçar e corrigir com base no padrão da classe
    texto_corrigido = aplicar_mascara_posicional(texto_bruto, padrao_primario)

    if regex_primario.match(texto_corrigido):
        return texto_corrigido, conf_media, NOMES_CLASSES.get(classe_detectada, classe_detectada)

    # 2. Fallback: Tenta a máscara oposta caso o detector tenha errado a classe
    padrao_alt = "LLLNNNN" if eh_mercosul else "LLLNLNN"
    regex_alt  = REGEX_ANTIGA if eh_mercosul else REGEX_MERCOSUL
    texto_alt  = aplicar_mascara_posicional(texto_bruto, padrao_alt)

    if regex_alt.match(texto_alt):
        tipo_fallback = "Antiga (Fallback)" if eh_mercosul else "Mercosul (Fallback)"
        return texto_alt, conf_media, tipo_fallback

    # Retorna o texto corrigido mesmo se não validar 100% no Regex
    return texto_corrigido if texto_corrigido else texto_bruto, conf_media, f"{NOMES_CLASSES.get(classe_detectada, classe_detectada)} (Inválida)"


# ------------------------------- OCR -----------------------------------
class OCR:
    def __init__(self, motor: str, gpu: bool = True):
        self.motor = motor.lower()
        if self.motor == "easyocr":
            import easyocr
            self.reader = easyocr.Reader(["en"], gpu=gpu)
        elif self.motor == "paddle":
            from paddleocr import PaddleOCR
            try:
                self.reader = PaddleOCR(
                    lang="en",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    enable_mkldnn=False,
                )
                self.v3 = True
            except (TypeError, ValueError):
                self.reader = PaddleOCR(lang="en", use_angle_cls=False, use_gpu=gpu, show_log=False)
                self.v3 = False
        else:
            raise ValueError("MOTOR_OCR deve ser 'easyocr' ou 'paddle'")

    def ler(self, crop_bgr: np.ndarray, classe_detectada: str) -> tuple[str, float, str]:
        if self.motor == "easyocr":
            res = self.reader.readtext(
                crop_bgr, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            )
            res = sorted(res, key=lambda r: (round(r[0][0][1] / FAIXA_LINHA_PX), r[0][0][0]))
            textos = [r[1] for r in res]
            confs = [r[2] for r in res]
            return processar_leitura(textos, confs, classe_detectada)

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


# ---------------------------- DETECÇÃO ---------------------------------
def detectar_placas(modelo, img_bgr):
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


def montar_painel(crops_tratados, resultados, caixas):
    blocos = []
    for crop_tratado, (texto, conf_ocr, tipo_exibicao), (_, _, _, _, conf_det, _) in zip(crops_tratados, resultados, caixas):
        c = redimensionar_largura(crop_tratado, LARGURA_PAINEL)
        c = c[:ALTURA_MAX_RECORTE]
        barra = np.full((120, LARGURA_PAINEL, 3), 30, np.uint8)
        
        cor_texto = (0, 255, 0) if "Inválida" not in tipo_exibicao and "Sem Leitura" not in tipo_exibicao else (0, 255, 255)

        cv2.putText(barra, texto if texto else "(sem leitura)", (10, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, cor_texto, 2, cv2.LINE_AA)
        cv2.putText(barra, f"det {conf_det:.2f} | ocr {conf_ocr:.2f}", (10, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(barra, f"Tipo: {tipo_exibicao}", (10, 106),
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
        escritor.writerow(["imagem", "placa_lida", "tipo_detectado", "conf_det", "conf_ocr"])

        for i, caminho in enumerate(imagens, 1):
            img = cv2.imread(str(caminho))
            if img is None:
                print(f"[!] não consegui abrir {caminho}")
                continue

            caixas, crops_tratados, resultados = [], [], []
            for caixa in detectar_placas(detector, img):
                x1, y1, x2, y2, _, nome_classe = caixa
                crop = img[max(y1, 0):y2, max(x1, 0):x2]
                if crop.size == 0:
                    continue

                crop_tratado = pre_processar_placa(crop)

                caixas.append(caixa)
                crops_tratados.append(crop_tratado)
                resultados.append(ocr.ler(crop_tratado, nome_classe))

            mosaico = juntar(desenhar_deteccoes(img, caixas),
                             montar_painel(crops_tratados, resultados, caixas))
            
            # Salva o mosaico em disco
            cv2.imwrite(str(saida / f"{caminho.stem}_mosaico.jpg"), mosaico)

            for (placa, conf_ocr, tipo), (_, _, _, _, conf_det, _) in zip(resultados, caixas):
                escritor.writerow([caminho.name, placa, tipo, f"{conf_det:.3f}", f"{conf_ocr:.3f}"])

            print(f"[{i}/{len(imagens)}] {caminho.name} -> {[(r[0], r[2]) for r in resultados]}")

            # Se MOSTRAR_JANELA for True, exibe na tela. Como está False, apenas processa em lote.
            if MOSTRAR_JANELA:
                cv2.imshow("Deteccao de placa + OCR", mosaico)
                if cv2.waitKey(0) & 0xFF in (ord("q"), 27):
                    break

    cv2.destroyAllWindows()
    print("Processamento concluído. Mosaicos e resultados salvos em:", SAIDA := PASTA_SAIDA)


if __name__ == "__main__":
    main()