"""
Pipeline ALPR Personalizado:
1. Detecção e classificação de placas via YOLO próprio (4 classes)
2. Mapeamento explícito de classes (1L/2L BR/ME)
3. Pré-processamento de imagem no recorte (Escala de Cinza, CLAHE e Filtro Bilateral)
4. OCR especializado via Fast-ALPR (DefaultOCR)
5. Aplicação rígida de máscara posicional e validação por Regex
6. Geração de mosaicos e exportação CSV
7. Geração dos eventos ods.i7.plate_read (um por leitura aceita) em eventos.jsonl
   (Fase A do barramento: valida o contrato e grava em arquivo, sem rede)
8. Experimentos de OCR controlados pelo .env (todos neutros por padrão): PREPROCESSAMENTO,
   MARGEM_RECORTE_PCT, DOBRAR_2L, DOBRAR_DIVISAO_PCT, OCR_MODELO. Cada execução grava um
   experimento.json com a configuração usada, e o resultados.csv inclui o texto BRUTO do OCR.
"""
import argparse
import csv
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv
from fast_alpr.default_ocr import DefaultOCR
from ultralytics import YOLO

from publicador_eventos import ConstrutorEventos, agora_utc, criar_publicador

# =========================== VARIÁVEIS GLOBAIS ===========================
RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

# PASTA_IMAGENS aceita UMA pasta ou VÁRIAS separadas por vírgula (ver listar_pastas).
PASTAS_IMAGENS_BRUTO = os.getenv("PASTA_IMAGENS", "datasetConvertidoYOLO")
PASTA_SAIDA = RAIZ / os.getenv("PASTA_SAIDA", "saidas")
CAMINHO_MODELO_YOLO = RAIZ / os.getenv("CAMINHO_MODELO_YOLO", "modelos/modelo4classesV1/best.pt")



def env_sim_nao(nome: str, padrao: str) -> bool:
    """Lê uma variável de ambiente 'sim/nao' (aceita também 1/0, true/false, s/n)."""
    valor = os.getenv(nome, padrao).strip().lower()
    return valor in ("1", "true", "sim", "s", "yes", "y")


def env_numero(nome: str, padrao: float) -> float:
    """Lê uma variável de ambiente numérica; erro claro se o valor não for um número."""
    valor = os.getenv(nome, "").strip()
    if not valor:
        return padrao
    try:
        return float(valor.replace(",", "."))
    except ValueError:
        raise SystemExit(f"[ERRO] {nome} deve ser um número, mas está como '{valor}'.")


# --- Experimentos de OCR (todos neutros por padrão = comportamento original) ---
PREPROCESSAMENTO = env_sim_nao("PREPROCESSAMENTO", "sim")   # cinza + 2x + CLAHE + filtro antes do OCR
MARGEM_RECORTE_PCT = env_numero("MARGEM_RECORTE_PCT", 0)    # % da caixa somada em cada lado do recorte
# Margem só para as classes Mercosul (..._ME). Se vazia, todas as classes usam MARGEM_RECORTE_PCT.
MARGEM_RECORTE_MERCOSUL_PCT = (env_numero("MARGEM_RECORTE_MERCOSUL_PCT", 0)
                               if os.getenv("MARGEM_RECORTE_MERCOSUL_PCT", "").strip() else None)
DOBRAR_2L = os.getenv("DOBRAR_2L", "nao").strip().lower()    # nao | auto | fixo (placas de 2 linhas)
DOBRAR_DIVISAO_PCT = env_numero("DOBRAR_DIVISAO_PCT", 55)   # altura (%) onde termina a 1ª linha
DOBRAR_FOLGA_PCT = env_numero("DOBRAR_FOLGA_PCT", 0)         # % da altura que a 2ª linha "sobe" além do corte
DOBRAR_DESCARTE_TOPO_PCT = env_numero("DOBRAR_DESCARTE_TOPO_PCT", 0)  # % da altura descartada no TOPO (faixa BRASIL)
OCR_MODELO = os.getenv("OCR_MODELO", "global-plates-mobile-vit-v2-model")  # modelo do hub do Fast-ALPR

# --- Publicação de eventos (barramento) ---
PUBLICACAO_MODO = os.getenv("PUBLICACAO_MODO", "arquivo")   # "arquivo", "kafka" ou "desligado"
PUBLICACAO_MOCKAR_PENDENTES = os.getenv("PUBLICACAO_MOCKAR_PENDENTES", "nao").strip().lower() in (
    "1", "true", "sim", "s", "yes")   # True: preenche character_confidences/uncertain com valores FALSOS
SOURCE_ID = os.getenv("SOURCE_ID", "cam-entrada")           # câmera/portão de origem
ARTIFACT_VERSION = os.getenv("ARTIFACT_VERSION") or (
    f"{CAMINHO_MODELO_YOLO.parent.name}+{OCR_MODELO.removesuffix('-model')}")  # modelo + OCR usados

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

# Contrato ods.i7.plate_read: layout da placa e classe do veículo a partir das 4 classes do YOLO.
# ATENÇÃO: "class" assume 1 linha = car (carros, caminhonetes, caminhões) e 2 linhas = motorcycle
# (motos, triciclos). É uma aproximação a confirmar com o TRA-1.
CLASSES_PARA_CONTRATO = {
    "placa_1L_BR": ("OLD_BR", "car"),
    "placa_1L_ME": ("MERCOSUL", "car"),
    "placa_2L_BR": ("OLD_BR", "motorcycle"),
    "placa_2L_ME": ("MERCOSUL", "motorcycle"),
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


def confianca_minima(confidence) -> float:
    """Menor confiança entre os caracteres (ou o próprio valor, se o OCR só entrega uma)."""
    if isinstance(confidence, (list, tuple)):
        return float(min(confidence)) if confidence else 0.0
    return float(confidence)


def formatar_confiancas(confidence) -> str:
    """Confianças por caractere como texto 'c1|c2|...' (ou o valor único), para o CSV."""
    if isinstance(confidence, (list, tuple)):
        return "|".join(f"{float(c):.3f}" for c in confidence)
    return f"{float(confidence):.3f}"


# ------------------- EXPERIMENTOS: recorte e placas de 2 linhas -------------------
def margem_para_classe(nome_classe: str) -> float:
    """Margem (%) do recorte para a classe do YOLO: a específica das Mercosul (_ME), se definida."""
    if MARGEM_RECORTE_MERCOSUL_PCT is not None and nome_classe.endswith("_ME"):
        return MARGEM_RECORTE_MERCOSUL_PCT
    return MARGEM_RECORTE_PCT


def expandir_caixa(x1, y1, x2, y2, largura_img, altura_img, pct):
    """Soma `pct`% da largura/altura da caixa em cada lado, limitando às bordas da imagem."""
    mx, my = (x2 - x1) * pct / 100.0, (y2 - y1) * pct / 100.0
    return (max(0, int(round(x1 - mx))), max(0, int(round(y1 - my))),
            min(largura_img, int(round(x2 + mx))), min(altura_img, int(round(y2 + my))))


def encontrar_divisao_linhas(crop_bgr: np.ndarray, divisao_padrao_pct: float) -> int:
    """
    Acha a altura (em pixels) onde termina a 1ª linha de uma placa de 2 linhas: o "vale" do
    perfil horizontal de pixels escuros, entre 35% e 75% da altura. Se não houver um vale
    claro (imagem muito apagada, por exemplo), usa `divisao_padrao_pct`.
    """
    h, w = crop_bgr.shape[:2]
    padrao = int(round(h * divisao_padrao_pct / 100.0))
    if h < 12 or w < 12:
        return padrao
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    bloco = max(11, (min(h, w) // 3) | 1)  # tamanho ímpar da vizinhança do limiar adaptativo
    binaria = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                    cv2.THRESH_BINARY_INV, bloco, 8)
    # ignora as bordas laterais (moldura, parafusos) e mede a "quantidade de tinta" de cada linha
    perfil = binaria[:, int(w * 0.12):int(w * 0.88)].mean(axis=1) / 255.0
    k = max(3, h // 12)
    suave = np.convolve(perfil, np.ones(k) / k, mode="same")
    ini, fim = int(h * 0.35), int(h * 0.75)
    janela = suave[ini:fim]
    if janela.size == 0:
        return padrao
    pico = float(np.percentile(suave, 90))
    if pico <= 0 or float(janela.min()) > 0.6 * pico:  # sem vale claro entre as duas linhas
        return padrao
    fundo = np.where(janela <= janela.min() + 0.02)[0]  # meio do vale (pode ser largo)
    return ini + int(np.median(fundo))


def dobrar_placa_2_linhas(crop_bgr: np.ndarray, modo: str, divisao_pct: float,
                          folga_pct: float = 0.0, descarte_topo_pct: float = 0.0) -> np.ndarray:
    """
    Corta a placa de 2 linhas entre as linhas e as coloca LADO A LADO (1ª linha à esquerda),
    formando uma imagem larga, parecida com uma placa de 1 linha. Assim o OCR não precisa
    esticar uma placa quase quadrada para a proporção 2:1 que ele espera.
    modo "auto": acha o corte pelo vale entre as linhas; "fixo": usa `divisao_pct` da altura.
    `folga_pct`: a metade de baixo começa `folga_pct`% da altura ANTES do corte, para não cortar
    o topo dos caracteres da 2ª linha quando o corte cai alguns pixels abaixo do ideal.
    `descarte_topo_pct`: joga fora esse % da altura no TOPO da placa (a faixa azul "BRASIL" das
    Mercosul, ou a tarja da cidade nas antigas), que não tem caracteres úteis e faz as letras da
    1ª linha ficarem menores que os dígitos da 2ª depois de igualar as alturas.
    """
    h = crop_bgr.shape[0]
    if h < 4:
        return crop_bgr
    if modo == "auto":
        corte = encontrar_divisao_linhas(crop_bgr, divisao_pct)
    else:
        corte = int(round(h * divisao_pct / 100.0))
    corte = min(max(corte, 2), h - 2)
    inicio_base = max(0, corte - int(round(h * folga_pct / 100.0)))
    inicio_topo = min(int(round(h * descarte_topo_pct / 100.0)), max(0, corte - 2))
    topo, base = crop_bgr[inicio_topo:corte], crop_bgr[inicio_base:]
    altura = max(topo.shape[0], base.shape[0])

    def ajustar(parte):
        largura = max(1, int(round(parte.shape[1] * altura / parte.shape[0])))
        return cv2.resize(parte, (largura, altura), interpolation=cv2.INTER_CUBIC)

    return np.hstack([ajustar(topo), ajustar(base)])


def validar_config_experimento() -> list[str]:
    erros = []
    if DOBRAR_2L not in ("nao", "auto", "fixo"):
        erros.append(f"DOBRAR_2L deve ser 'nao', 'auto' ou 'fixo' (recebi '{DOBRAR_2L}')")
    if not 0 <= MARGEM_RECORTE_PCT <= 50:
        erros.append(f"MARGEM_RECORTE_PCT deve estar entre 0 e 50 (recebi {MARGEM_RECORTE_PCT})")
    if MARGEM_RECORTE_MERCOSUL_PCT is not None and not 0 <= MARGEM_RECORTE_MERCOSUL_PCT <= 50:
        erros.append(f"MARGEM_RECORTE_MERCOSUL_PCT deve estar entre 0 e 50 (recebi {MARGEM_RECORTE_MERCOSUL_PCT})")
    if not 20 <= DOBRAR_DIVISAO_PCT <= 80:
        erros.append(f"DOBRAR_DIVISAO_PCT deve estar entre 20 e 80 (recebi {DOBRAR_DIVISAO_PCT})")
    if not 0 <= DOBRAR_FOLGA_PCT <= 20:
        erros.append(f"DOBRAR_FOLGA_PCT deve estar entre 0 e 20 (recebi {DOBRAR_FOLGA_PCT})")
    if not 0 <= DOBRAR_DESCARTE_TOPO_PCT <= 40:
        erros.append(f"DOBRAR_DESCARTE_TOPO_PCT deve estar entre 0 e 40 (recebi {DOBRAR_DESCARTE_TOPO_PCT})")
    if not OCR_MODELO.strip():
        erros.append("OCR_MODELO está vazio")
    return erros


def config_experimento(pasta: Path, limite: int | None) -> dict:
    """Configuração desta execução, gravada em experimento.json ao lado dos resultados."""
    return {
        "data_hora": datetime.now().isoformat(timespec="seconds"),
        "pasta_imagens": str(pasta),
        "limite": limite,
        "modelo_yolo": str(CAMINHO_MODELO_YOLO),
        "conf_deteccao": CONF_DETECCAO,
        "preprocessamento": PREPROCESSAMENTO,
        "margem_recorte_pct": MARGEM_RECORTE_PCT,
        "margem_recorte_mercosul_pct": MARGEM_RECORTE_MERCOSUL_PCT,
        "dobrar_2l": DOBRAR_2L,
        "dobrar_divisao_pct": DOBRAR_DIVISAO_PCT,
        "dobrar_folga_pct": DOBRAR_FOLGA_PCT,
        "dobrar_descarte_topo_pct": DOBRAR_DESCARTE_TOPO_PCT,
        "ocr_modelo": OCR_MODELO,
    }


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


# ------------------------- PASTAS DE ENTRADA ---------------------------
def listar_pastas(valor: str) -> list[Path]:
    """
    Converte o valor de PASTA_IMAGENS em uma lista de pastas.
    Aceita UMA pasta ou VÁRIAS separadas por vírgula; espaços ao redor, aspas e itens
    vazios são ignorados. Caminhos relativos partem da RAIZ do projeto (um caminho
    absoluto é usado como está).
        "a/images/test"                     -> [RAIZ/a/images/test]
        "a/images/test, b/images/test"      -> [RAIZ/a/images/test, RAIZ/b/images/test]
    """
    pastas = []
    for item in valor.split(","):
        item = item.strip().strip('"').strip("'").strip()
        if not item:
            continue
        pasta = RAIZ / item
        if pasta not in pastas:  # ignora repetidas
            pastas.append(pasta)
    return pastas


def rotulos_das_pastas(pastas: list[Path]) -> list[str]:
    """
    Nome curto e único para cada pasta, usado como subpasta de saída quando há mais de uma.
    Remove o trecho que TODAS têm em comum no começo e no fim do caminho:
        .../datasetConvertidoYOLO/car-br/images/test  ->  car-br
        .../datasetConvertidoYOLO/moto-me/images/test ->  moto-me
    """
    partes = [list(p.resolve().parts) for p in pastas]

    ini = 0  # quantos componentes iguais no começo
    while all(len(x) > ini for x in partes) and len({x[ini] for x in partes}) == 1:
        ini += 1

    fim = 0  # quantos componentes iguais no fim (sem invadir o começo comum)
    while all(len(x) - fim > ini for x in partes) and len({x[-1 - fim] for x in partes}) == 1:
        fim += 1

    rotulos = ["_".join(x[ini:len(x) - fim]) for x in partes]
    if "" in rotulos or len(set(rotulos)) != len(rotulos):
        # caso raro (uma pasta dentro da outra): usa o caminho a partir do trecho inicial comum
        rotulos = ["_".join(x[ini:]) or x[-1] for x in partes]
    return rotulos


# ------------------------------- MAIN ----------------------------------
def processar_pasta(pasta: Path, saida: Path, modelo_yolo, ocr, construtor: ConstrutorEventos,
                    limite: int | None = None) -> bool:
    """
    Roda o pipeline nas imagens de `pasta` (busca recursiva) e grava os mosaicos, o
    resultados.csv e (conforme PUBLICACAO_MODO) o eventos.jsonl em `saida`. Cada leitura aceita
    gera um evento; a linha N do eventos.jsonl corresponde às leituras aceitas do CSV, e o
    `frame_index` do evento é a posição (a partir de 0) da imagem na lista ordenada da pasta.
    Com `limite`, processa só as N primeiras (ordem alfabética do caminho), para testes rápidos.
    Devolve True se o usuário interrompeu pela janela (q/ESC).
    """
    todas = sorted(p for p in pasta.rglob("*") if p.suffix.lower() in EXTENSOES)
    imagens = todas[:limite] if limite is not None else todas
    aviso = (f" | MODO TESTE: processando só {len(imagens)} de {len(todas)}"
             if len(imagens) < len(todas) else "")
    print(f"\n[{pasta}] {len(todas)} imagens encontradas{aviso}")
    if not imagens:
        print("[!] Nenhuma imagem nesta pasta, pulando.")
        return False
    saida.mkdir(parents=True, exist_ok=True)
    # Registra COMO esta execução foi feita, para poder comparar experimentos depois.
    (saida / "experimento.json").write_text(
        json.dumps(config_experimento(pasta, limite), ensure_ascii=False, indent=2), encoding="utf-8")

    interrompido = False
    with open(saida / "resultados.csv", "w", newline="", encoding="utf-8") as arq_csv, \
            criar_publicador(PUBLICACAO_MODO, saida / "eventos.jsonl",
                             exigir_pendentes=PUBLICACAO_MOCKAR_PENDENTES) as publicador:
        escritor = csv.writer(arq_csv)
        escritor.writerow(["imagem", "placa_lida", "tipo_detectado", "conf_det", "conf_ocr",
                           "texto_ocr_bruto", "conf_ocr_min", "conf_ocr_por_char"])

        for i, caminho in enumerate(imagens, 1):
            img = cv2.imread(str(caminho))
            if img is None:
                print(f"[!] Não foi possível abrir {caminho}")
                continue
            capturado_em = agora_utc()  # instante da captura (aqui: leitura da imagem do disco)

            # Inferencia no detector YOLO
            yolo_results = modelo_yolo.predict(img, conf=CONF_DETECCAO, verbose=False)[0]

            caixas = []
            crops = []
            resultados = []
            extras = []  # por placa, alinhada às listas acima: (texto bruto, confiança mínima, confianças)

            for box in yolo_results.boxes:
                conf_det = float(box.conf[0])
                cls_id = int(box.cls[0])
                nome_classe_raw = modelo_yolo.names[cls_id]

                # Mapeia para o nome formatado usando o dicionário NOMES_CLASSES
                rotulo_classe = NOMES_CLASSES.get(nome_classe_raw, nome_classe_raw)

                # Extrai coordenadas Bounding Box
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                # Recorte da placa (com a margem do experimento; 0% = exatamente a caixa do YOLO)
                cx1, cy1, cx2, cy2 = expandir_caixa(x1, y1, x2, y2, img.shape[1], img.shape[0],
                                                    margem_para_classe(nome_classe_raw))
                crop_original = img[cy1:cy2, cx1:cx2]
                if crop_original.size == 0:
                    continue

                # Experimento: placa de 2 linhas "dobrada" em uma linha larga
                entrada_ocr = crop_original
                if DOBRAR_2L != "nao" and nome_classe_raw.startswith("placa_2L"):
                    entrada_ocr = dobrar_placa_2_linhas(crop_original, DOBRAR_2L, DOBRAR_DIVISAO_PCT,
                                                        DOBRAR_FOLGA_PCT, DOBRAR_DESCARTE_TOPO_PCT)

                # Pré-processamento antes da chamada do OCR (pode ser desligado: PREPROCESSAMENTO=nao)
                crop_processado = pre_processar_recorte_ocr(entrada_ocr) if PREPROCESSAMENTO else entrada_ocr

                # OCR no recorte pré-processado
                ocr_result = ocr.predict(crop_processado)

                texto_ocr = ""
                conf_ocr = 0.0
                conf_min = 0.0
                confs_txt = ""

                if ocr_result:
                    texto_ocr = ocr_result.text            # texto BRUTO, antes de qualquer correção
                    conf_ocr = normalizar_confianca_ocr(ocr_result.confidence)
                    conf_min = confianca_minima(ocr_result.confidence)
                    confs_txt = formatar_confiancas(ocr_result.confidence)

                # Tratamento e validação de regras com base no rótulo mapeado
                placa_validada, status_tipo = processar_leitura(texto_ocr, rotulo_classe)

                # Evento ods.i7.plate_read, só para leituras ACEITAS pela validação
                # (processar_leitura devolve o rótulo da classe sem sufixo quando a placa é válida).
                if (publicador.ativo and status_tipo == rotulo_classe
                        and nome_classe_raw in CLASSES_PARA_CONTRATO):
                    layout_placa, classe_veiculo = CLASSES_PARA_CONTRATO[nome_classe_raw]
                    publicador.publicar(construtor.plate_read(
                        placa=placa_validada, plate_layout=layout_placa, classe_veiculo=classe_veiculo,
                        confianca=conf_min,  # MENOR confiança entre os caracteres (separa melhor leituras boas das ruins que a média)
                        bbox=[max(x1, 0), max(y1, 0), x2, y2], frame_index=i - 1,
                        captured_at=capturado_em))

                caixas.append((x1, y1, x2, y2, conf_det, status_tipo))
                crops.append(entrada_ocr)  # no mosaico: o que o OCR "viu" (com margem / dobrada, se for o caso)
                resultados.append((placa_validada, conf_ocr, status_tipo, conf_det))
                extras.append((texto_ocr, conf_min, confs_txt))

            mosaico = juntar(desenhar_deteccoes(img, caixas),
                             montar_painel(crops, resultados))
            
            # Salva o mosaico
            cv2.imwrite(str(saida / f"{caminho.stem}_mosaico.jpg"), mosaico)

            for (placa, conf_ocr, tipo, conf_det), (bruto, conf_min, confs_txt) in zip(resultados, extras):
                escritor.writerow([caminho.name, placa, tipo, f"{conf_det:.3f}", f"{conf_ocr:.3f}",
                                   bruto, f"{conf_min:.3f}", confs_txt])

            print(f"[{i}/{len(imagens)}] {caminho.name} -> {[r[0] for r in resultados]}")

            if MOSTRAR_JANELA:
                cv2.imshow("Deteccao YOLO + Fast-ALPR OCR", mosaico)
                if cv2.waitKey(0) & 0xFF in (ord("q"), 27):
                    interrompido = True
                    break

    print(f"Resultados desta pasta salvos em: {saida}")
    print(f"Publicação: {publicador.resumo()}")
    return interrompido


def main(limite: int | None = None):
    pastas = listar_pastas(PASTAS_IMAGENS_BRUTO)
    if not pastas:
        raise SystemExit("[ERRO] PASTA_IMAGENS está vazia. Informe uma pasta ou uma lista separada por vírgula.")

    ausentes = [p for p in pastas if not p.is_dir()]
    if ausentes:
        raise SystemExit("[ERRO] Pasta(s) de imagens não encontrada(s):\n  " +
                         "\n  ".join(str(p) for p in ausentes))

    # Uma pasta: grava direto em PASTA_SAIDA. Várias: uma subpasta de saída por pasta de entrada.
    if len(pastas) == 1:
        saidas = [PASTA_SAIDA]
    else:
        saidas = [PASTA_SAIDA / rotulo for rotulo in rotulos_das_pastas(pastas)]

    # Confere a configuração ANTES de carregar os modelos (que demora).
    erros_config = validar_config_experimento()
    if erros_config:
        raise SystemExit("[ERRO] Configuração inválida:\n  " + "\n  ".join(erros_config))
    try:
        criar_publicador(PUBLICACAO_MODO, PASTA_SAIDA / "eventos.jsonl")
    except (ValueError, NotImplementedError) as erro:
        raise SystemExit(f"[ERRO] {erro}")
    construtor = ConstrutorEventos(SOURCE_ID, ARTIFACT_VERSION, PUBLICACAO_MOCKAR_PENDENTES)

    print(f"Detector: YOLO Próprio | OCR: Fast-ALPR ({OCR_MODELO}) | {len(pastas)} pasta(s) de imagens")
    print(f"Experimento: preprocessamento={'sim' if PREPROCESSAMENTO else 'nao'} | "
          f"margem={MARGEM_RECORTE_PCT:g}%"
          + (f" (Mercosul: {MARGEM_RECORTE_MERCOSUL_PCT:g}%)" if MARGEM_RECORTE_MERCOSUL_PCT is not None else "")
          + f" | dobrar_2l={DOBRAR_2L}"
          + (f" (divisão {DOBRAR_DIVISAO_PCT:g}%, folga {DOBRAR_FOLGA_PCT:g}%, "
             f"descarte do topo {DOBRAR_DESCARTE_TOPO_PCT:g}%)" if DOBRAR_2L != "nao" else ""))
    print(f"Publicação: modo={PUBLICACAO_MODO} | source_id={SOURCE_ID} | "
          f"artifact_version={ARTIFACT_VERSION} | session_id={construtor.session_id}")
    if PUBLICACAO_MOCKAR_PENDENTES:
        print("[!] character_confidences e uncertain estão sendo MOCKADOS (valores falsos).")
    for pasta, saida in zip(pastas, saidas):
        print(f"  {pasta}  ->  {saida}")

    # 1. Carrega modelo YOLO treinado (uma vez, para todas as pastas)
    modelo_yolo = YOLO(CAMINHO_MODELO_YOLO)

    # 2. Inicializa o OCR (uma vez)
    ocr = DefaultOCR(hub_ocr_model=OCR_MODELO)

    for pasta, saida in zip(pastas, saidas):
        if processar_pasta(pasta, saida, modelo_yolo, ocr, construtor, limite):
            break  # usuário pediu para sair

    if MOSTRAR_JANELA:  # em OpenCV "headless" esta chamada daria erro
        cv2.destroyAllWindows()
    print("\nProcessamento concluído.")


def inteiro_positivo(texto: str) -> int:
    try:
        valor = int(texto)
    except ValueError:
        valor = 0
    if valor < 1:
        raise argparse.ArgumentTypeError(f"'{texto}' não é um inteiro positivo")
    return valor


def ler_argumentos():
    parser = argparse.ArgumentParser(
        description="Pipeline ALPR: YOLO próprio (4 classes) + OCR Fast-ALPR. "
                    "As pastas e os caminhos vêm do .env (PASTA_IMAGENS, PASTA_SAIDA, CAMINHO_MODELO_YOLO).")
    parser.add_argument(
        "-n", "--limite", type=inteiro_positivo, default=None, metavar="N",
        help="modo teste: processa só as N primeiras imagens de cada pasta "
             "(sem este argumento, processa todas)")
    return parser.parse_args()


if __name__ == "__main__":
    main(ler_argumentos().limite)