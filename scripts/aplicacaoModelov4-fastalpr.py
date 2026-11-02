"""
Pipeline ALPR com Fast-ALPR:
1. Detecção e OCR de alta precisão especializado em placas via FastALPR
2. Mapeamento de classes e pós-processamento de formato (Mercosul / Antiga)
3. Validação por Regex e geração de mosaico + CSV (Modo Headless/Apenas Salvar)
"""
import csv
import math
import re
from pathlib import Path

import cv2
import numpy as np
from fast_alpr import ALPR

# =========================== VARIÁVEIS GLOBAIS ===========================
PASTA_IMAGENS = "/media/gabriel/HD-Gabriel/ODS/imagensTeste"          # pasta com as imagens de teste
PASTA_SAIDA = "/media/gabriel/HD-Gabriel/ODS/saida_mosaicos/saida4classesV4-fastalpr"          # mosaicos + resultados.csv

CONF_DETECCAO = 0.25                     # confiança mínima do detector
MOSTRAR_JANELA = False                   # False = Apenas salva (sem janelas na tela)
ALTURA_MOSAICO = 640                     # altura da imagem principal no mosaico
LARGURA_PAINEL = 420                     # largura do painel do recorte
ALTURA_MAX_RECORTE = 220                 # altura máxima do recorte no painel
EXTENSOES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Dicionários de Substituição para Correção OCR Posicional
NUMERO_PARA_LETRA = {'0': 'O', '1': 'I', '2': 'Z', '3': 'B', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B'}
LETRA_PARA_NUMERO = {'O': '0', 'Q': '0', 'D': '0', 'I': '1', 'L': '1', 'Z': '2', 'B': '8', 'S': '5', 'G': '6', 'T': '7', 'A': '4'}

# Regex de validação
REGEX_MERCOSUL = re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$")
REGEX_ANTIGA   = re.compile(r"^[A-Z]{3}[0-9]{4}$")
# =========================================================================


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


def processar_leitura(texto_bruto):
    """
    Higieniza a leitura do Fast-ALPR, aplica a máscara posicional e valida via Regex.
    """
    if not texto_bruto:
        return "", "Sem Leitura"

    # Limpeza básica de caracteres
    limpo = re.sub(r"[^A-Z0-9]", "", texto_bruto.upper())
    limpo = limpo.replace("BRASIL", "")

    # Teste 1: Tenta padrão Mercosul (LLLNLNN)
    texto_mercosul = aplicar_mascara_posicional(limpo, "LLLNLNN")
    if REGEX_MERCOSUL.match(texto_mercosul):
        return texto_mercosul, "Mercosul"

    # Teste 2: Tenta padrão Antiga (LLLNNNN)
    texto_antiga = aplicar_mascara_posicional(limpo, "LLLNNNN")
    if REGEX_ANTIGA.match(texto_antiga):
        return texto_antiga, "Antiga"

    return texto_mercosul if texto_mercosul else limpo, "Desconhecido (Inválida)"


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
    print(f"{len(imagens)} imagens encontradas | Motor: Fast-ALPR")

    # Inicializa o pipeline do Fast-ALPR (Detector + OCR especializado)
    alpr = ALPR(
        detector_model="yolo-v9-t-384-license-plate-end2end",
        ocr_model="global-plates-mobile-vit-v2-model"
    )

    with open(saida / "resultados.csv", "w", newline="", encoding="utf-8") as arq_csv:
        escritor = csv.writer(arq_csv)
        escritor.writerow(["imagem", "placa_lida", "tipo_detectado", "conf_det", "conf_ocr"])

        for i, caminho in enumerate(imagens, 1):
            img = cv2.imread(str(caminho))
            if img is None:
                print(f"[!] Não foi possível abrir {caminho}")
                continue

            # Processa a imagem pelo Fast-ALPR
            alpr_results = alpr.predict(img)

            caixas = []
            crops = []
            resultados = []

            for res in alpr_results:
                conf_det = res.detection.confidence
                if conf_det < CONF_DETECCAO:
                    continue

                # Extrai as coordenadas da caixa delimitadora
                x1, y1, x2, y2 = res.detection.bounding_box.x1, res.detection.bounding_box.y1, res.detection.bounding_box.x2, res.detection.bounding_box.y2
                x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])

                crop = img[max(y1, 0):y2, max(x1, 0):x2]
                if crop.size == 0:
                    continue

                texto_ocr = res.ocr.text if res.ocr else ""
                conf_ocr = normalizar_confianca_ocr(res.ocr.confidence) if res.ocr else 0.0

                # Aplica tratamento e validação posicional de caracteres
                placa_validada, tipo_placa = processar_leitura(texto_ocr)

                caixas.append((x1, y1, x2, y2, conf_det, tipo_placa))
                crops.append(crop)
                resultados.append((placa_validada, conf_ocr, tipo_placa, conf_det))

            mosaico = juntar(desenhar_deteccoes(img, caixas),
                             montar_painel(crops, resultados))
            
            # Salva o mosaico em disco
            cv2.imwrite(str(saida / f"{caminho.stem}_mosaico.jpg"), mosaico)

            for (placa, conf_ocr, tipo, conf_det) in resultados:
                escritor.writerow([caminho.name, placa, tipo, f"{conf_det:.3f}", f"{conf_ocr:.3f}"])

            print(f"[{i}/{len(imagens)}] {caminho.name} -> {[r[0] for r in resultados]}")

            if MOSTRAR_JANELA:
                cv2.imshow("Deteccao de placa + Fast-ALPR", mosaico)
                if cv2.waitKey(0) & 0xFF in (ord("q"), 27):
                    break

    cv2.destroyAllWindows()
    print("Processamento concluído. Mosaicos e resultados salvos em:", PASTA_SAIDA)


if __name__ == "__main__":
    main()