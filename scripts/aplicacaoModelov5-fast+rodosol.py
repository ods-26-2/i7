"""
Pipeline ALPR Personalizado:
1. Detecção e classificação de placas via YOLO próprio (4 classes)
2. Mapeamento explícito de classes (1L/2L BR/ME)
3. Pré-processamento de imagem no recorte (Escala de Cinza, CLAHE e Filtro Bilateral)
4. OCR especializado via Fast-ALPR (DefaultOCR)
5. Aplicação rígida de máscara posicional e validação por Regex
6. Geração de mosaicos e exportação CSV
"""
import csv
import math
import re
from pathlib import Path

import cv2
import numpy as np
from fast_alpr.default_ocr import DefaultOCR
from ultralytics import YOLO

# =========================== VARIÁVEIS GLOBAIS ===========================
PASTA_IMAGENS = "/home/gabriel/Desktop/ODS/ImgsTesteModelo"
PASTA_SAIDA = "/home/gabriel/Desktop/ODS/saida_mosaicos/aplicacaoModelov5-fast+rodosol"
CAMINHO_MODELO_YOLO = "/home/gabriel/Desktop/ODS/modelos/modelo4classesV1/best.pt"

CONF_DETECCAO = 0.25
MOSTRAR_JANELA = False
ALTURA_MOSAICO = 640
LARGURA_PAINEL = 420
ALTURA_MAX_RECORTE = 420
EXTENSOES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Mapeamento explícito das classes do seu modelo YOLO
NOMES_CLASSES = {
    "placa_1L_BR": "Antiga (1 Linha)",
    "placa_2L_BR": "Antiga (2 Linhas)",
    "placa_1L_ME": "Mercosul (1 Linha)",
    "placa_2L_ME": "Mercosul (2 Linhas)",
}

# Dicionários de Substituição para Correção OCR Posicional
NUMERO_PARA_LETRA = {'0': 'O', '1': 'I', '2': 'Z', '3': 'B', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B'}
LETRA_PARA_NUMERO = {'O': '0', 'Q': '0', 'D': '0', 'I': '1', 'L': '1', 'Z': '2', 'B': '8', 'S': '5', 'G': '6', 'T': '7', 'A': '4'}

# Regex de validação
REGEX_MERCOSUL = re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$")
REGEX_ANTIGA   = re.compile(r"^[A-Z]{3}[0-9]{4}$")
# =========================================================================


def pre_processar_recorte_ocr(crop: np.ndarray) -> np.ndarray:
    """
    Aplica técnicas de processamento de imagem para otimizar a leitura do OCR:
    1. Conversão para Tons de Cinza
    2. Redimensionamento para garantir resolução mínima
    3. Ajuste de contraste local com CLAHE
    4. Remoção de ruído preservando bordas (Filtro Bilateral)
    """
    # 1. Converter para escala de cinza
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # 2. Redimensionar se o recorte for muito pequeno (aumenta definição das letras)
    h, w = gray.shape
    if h < 80 or w < 240:
        gray = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

    # 3. CLAHE (Contrast Limited Adaptive Histogram Equalization) para realçar letras
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    equalizado = clahe.apply(gray)

    # 4. Filtro Bilateral para suavizar ruído sem borrar os contornos dos caracteres
    filtrado = cv2.bilateralFilter(equalizado, d=5, sigmaColor=75, sigmaSpace=75)

    # Reverte para 3 canais BGR pois o modelo de OCR espera imagem com 3 canais
    return cv2.cvtColor(filtrado, cv2.COLOR_GRAY2BGR)


def forcar_sete_caracteres(texto: str) -> str:
    """Garante uma string de exatamente 7 caracteres descartando ruídos no meio."""
    if len(texto) == 7:
        return texto

    if len(texto) > 7:
        candidato_sem_meio = texto[:3] + texto[4:]
        if len(candidato_sem_meio) == 7:
            return candidato_sem_meio
        return texto[:7]

    return texto


def aplicar_mascara_posicional(texto: str, padrao_esperado: str) -> str:
    """Força caractere a caractere a conversão com base no padrão (LLLNLNN ou LLLNNNN)."""
    texto_ajustado = forcar_sete_caracteres(texto)
    
    if len(texto_ajustado) != 7:
        return texto_ajustado

    resultado = []
    for char, tipo in zip(texto_ajustado, padrao_esperado):
        if tipo == 'L':
            resultado.append(NUMERO_PARA_LETRA.get(char, char))
        elif tipo == 'N':
            resultado.append(LETRA_PARA_NUMERO.get(char, char))
        else:
            resultado.append(char)

    return "".join(resultado)


def processar_leitura(texto_bruto: str, rotulo_classe: str):
    """
    Higieniza o texto lido pelo OCR e aplica a regra correspondente ao
    rótulo formatado da classe detectada.
    """
    if not texto_bruto:
        return "", f"{rotulo_classe} (Sem Leitura)"

    # Limpeza básica
    limpo = re.sub(r"[^A-Z0-9]", "", texto_bruto.upper())
    limpo = limpo.replace("BRASIL", "")

    # Checa o rótulo amigável mapeado
    if "Mercosul" in rotulo_classe:
        texto_validado = aplicar_mascara_posicional(limpo, "LLLNLNN")
        if REGEX_MERCOSUL.match(texto_validado):
            return texto_validado, rotulo_classe
        return texto_validado, f"{rotulo_classe} (Inválida)"

    elif "Antiga" in rotulo_classe:
        texto_validado = aplicar_mascara_posicional(limpo, "LLLNNNN")
        if REGEX_ANTIGA.match(texto_validado):
            return texto_validado, rotulo_classe
        return texto_validado, f"{rotulo_classe} (Inválida)"

    return limpo, f"{rotulo_classe} (Desconhecido)"


def normalizar_confianca_ocr(confidence: float | list[float]) -> float:
    """Resume as confianças por caractere do OCR numa pontuação média."""
    if isinstance(confidence, list):
        return math.fsum(confidence) / len(confidence) if confidence else 0.0
    return confidence


# ----------------------------- MOSAICO ---------------------------------
def redimensionar_largura(img, largura):
    h, w = img.shape[:2]
    return cv2.resize(img, (largura, max(1, int(h * largura / w))))


def montar_painel(crops, resultados):
    blocos = []
    for crop, (texto, conf_ocr, tipo, conf_det) in zip(crops, resultados):
        c = redimensionar_largura(crop, LARGURA_PAINEL)
        c = c[:ALTURA_MAX_RECORTE]
        barra = np.full((120, LARGURA_PAINEL, 3), 30, np.uint8)
        
        cor_texto = (0, 255, 0) if "Inválida" not in tipo and "Sem Leitura" not in tipo else (0, 255, 255)

        cv2.putText(barra, texto if texto else "(sem leitura)", (10, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, cor_texto, 2, cv2.LINE_AA)
        cv2.putText(barra, f"det {conf_det:.2f} | ocr {conf_ocr:.2f}", (10, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(barra, f"Tipo: {tipo}", (10, 106),
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
    for (x1, y1, x2, y2, _, tipo) in caixas:
        cv2.rectangle(anot, (x1, y1), (x2, y2), (0, 255, 0), 3)
        cv2.putText(anot, tipo, (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    h, w = anot.shape[:2]
    return cv2.resize(anot, (int(w * ALTURA_MOSAICO / h), ALTURA_MOSAICO))


# ------------------------------- MAIN ----------------------------------
def main():
    pasta = Path(PASTA_IMAGENS)
    saida = Path(PASTA_SAIDA)
    saida.mkdir(parents=True, exist_ok=True)

    imagens = sorted(p for p in pasta.rglob("*") if p.suffix.lower() in EXTENSOES)
    print(f"{len(imagens)} imagens encontradas | Detector: YOLO Próprio | OCR: Fast-ALPR")

    # 1. Carrega modelo YOLO treinado
    modelo_yolo = YOLO(CAMINHO_MODELO_YOLO)

    # 2. Inicializa o OCR
    ocr = DefaultOCR(hub_ocr_model="global-plates-mobile-vit-v2-model")

    with open(saida / "resultados.csv", "w", newline="", encoding="utf-8") as arq_csv:
        escritor = csv.writer(arq_csv)
        escritor.writerow(["imagem", "placa_lida", "tipo_detectado", "conf_det", "conf_ocr"])

        for i, caminho in enumerate(imagens, 1):
            img = cv2.imread(str(caminho))
            if img is None:
                print(f"[!] Não foi possível abrir {caminho}")
                continue

            # Inferencia no detector YOLO
            yolo_results = modelo_yolo.predict(img, conf=CONF_DETECCAO, verbose=False)[0]

            caixas = []
            crops = []
            resultados = []

            for box in yolo_results.boxes:
                conf_det = float(box.conf[0])
                cls_id = int(box.cls[0])
                nome_classe_raw = modelo_yolo.names[cls_id]

                # Mapeia para o nome formatado usando o dicionário NOMES_CLASSES
                rotulo_classe = NOMES_CLASSES.get(nome_classe_raw, nome_classe_raw)

                # Extrai coordenadas Bounding Box
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                crop_original = img[max(y1, 0):y2, max(x1, 0):x2]
                if crop_original.size == 0:
                    continue

                # Pré-processamento antes da chamada do OCR
                crop_processado = pre_processar_recorte_ocr(crop_original)

                # OCR no recorte pré-processado
                ocr_result = ocr.predict(crop_processado)

                texto_ocr = ""
                conf_ocr = 0.0

                if ocr_result:
                    texto_ocr = ocr_result.text
                    conf_ocr = normalizar_confianca_ocr(ocr_result.confidence)

                # Tratamento e validação de regras com base no rótulo mapeado
                placa_validada, status_tipo = processar_leitura(texto_ocr, rotulo_classe)

                caixas.append((x1, y1, x2, y2, conf_det, status_tipo))
                crops.append(crop_original)
                resultados.append((placa_validada, conf_ocr, status_tipo, conf_det))

            mosaico = juntar(desenhar_deteccoes(img, caixas),
                             montar_painel(crops, resultados))
            
            # Salva o mosaico
            cv2.imwrite(str(saida / f"{caminho.stem}_mosaico.jpg"), mosaico)

            for (placa, conf_ocr, tipo, conf_det) in resultados:
                escritor.writerow([caminho.name, placa, tipo, f"{conf_det:.3f}", f"{conf_ocr:.3f}"])

            print(f"[{i}/{len(imagens)}] {caminho.name} -> {[r[0] for r in resultados]}")

            if MOSTRAR_JANELA:
                cv2.imshow("Deteccao YOLO + Fast-ALPR OCR", mosaico)
                if cv2.waitKey(0) & 0xFF in (ord("q"), 27):
                    break

    cv2.destroyAllWindows()
    print("Processamento concluído. Mosaicos e resultados salvos em:", PASTA_SAIDA)


if __name__ == "__main__":
    main()
