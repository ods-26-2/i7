"""
Conversor de dataset de placas (formato UFPR-ALPR) para o formato YOLO (detecção / bounding box).

Formato de entrada (um .txt ao lado de cada imagem):
    type: car
    plate: ODE2510
    layout: Brazilian
    corners: 558,438 687,439 687,482 558,481

Formato de saída (YOLO detect, 1 arquivo .txt por imagem, valores normalizados 0-1):
    0 x_centro y_centro largura altura

Estrutura criada em DEST_DIR:
    DEST_DIR/
      data.yaml
      split_manifest.csv      (registro de qual imagem foi para qual split)
      images/{train,val,test}/
      labels/{train,val,test}/

A pasta de origem (SOURCE_DIR) NUNCA é alterada: as imagens são apenas copiadas
e os labels convertidos são gerados como arquivos novos no destino.
"""

import csv
import random
import re
import shutil
import sys
from pathlib import Path

from PIL import Image  # já vem instalado junto com o ultralytics

# =============================================================================
# CONFIGURAÇÕES GLOBAIS  (edite aqui)
# =============================================================================

# Pasta original do dataset (imagens + .txt). Será procurada recursivamente.
SOURCE_DIR = Path("/home/gabriel/Desktop/ODS/tbFcZE-RodoSol-ALPR/images/motorcycles-me")

# Pasta de destino, onde o dataset no formato YOLO será criado.
DEST_DIR = Path("/home/gabriel/Desktop/ODS/datasetConvertidoYOLO/moto-me")

# Proporções do split. Devem somar 1.0. Coloque 0.0 em uma delas para não criá-la.
TRAIN_RATIO = 0.70
VAL_RATIO = 0.20
TEST_RATIO = 0.10

# Semente do embaralhamento: mesma seed = mesmo split, sempre (reprodutível).
SEED = 42

# Agrupar por pasta de origem antes de sortear?
#   True  -> todas as imagens da mesma pasta (ex.: mesmo veículo/track) vão para o MESMO split.
#            Evita "vazamento de dados" (frames quase idênticos no treino e no teste),
#            que infla as métricas. Se o dataset for "plano" (1 pasta só), o script
#            cai automaticamente para o sorteio por imagem.
#   False -> sorteio puro imagem por imagem.
GROUP_BY_FOLDER = True

# Extensões de imagem aceitas.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}

# Nomes das classes (índice = posição). Aqui só existe "plate".
CLASS_NAMES = ["plate"]

# Margem (em pixels) adicionada em volta da placa no bounding box.
# 0 = caixa exatamente nos cantos. Um valor pequeno (2-4) pode ajudar a não cortar bordas.
BBOX_PADDING_PX = 0

# Se DEST_DIR já existir e não estiver vazia: True apaga e recria; False aborta.
OVERWRITE = False

# =============================================================================


def validar_configuracao():
    soma = TRAIN_RATIO + VAL_RATIO + TEST_RATIO
    if abs(soma - 1.0) > 1e-6:
        sys.exit(f"[ERRO] As proporções do split somam {soma}, deveriam somar 1.0.")
    if min(TRAIN_RATIO, VAL_RATIO, TEST_RATIO) < 0:
        sys.exit("[ERRO] Proporções não podem ser negativas.")
    if TRAIN_RATIO <= 0:
        sys.exit("[ERRO] TRAIN_RATIO precisa ser maior que 0.")
    if not SOURCE_DIR.is_dir():
        sys.exit(f"[ERRO] SOURCE_DIR não existe: {SOURCE_DIR}")

    # Proteção da pasta de origem: destino não pode ser a origem nem estar dentro dela
    # (e vice-versa), senão poderíamos misturar/apagar arquivos originais.
    src, dst = SOURCE_DIR.resolve(), DEST_DIR.resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        sys.exit("[ERRO] DEST_DIR e SOURCE_DIR não podem ser iguais nem estar uma dentro da outra.")


def preparar_destino():
    if DEST_DIR.exists() and any(DEST_DIR.iterdir()):
        if not OVERWRITE:
            sys.exit(f"[ERRO] {DEST_DIR} já existe e não está vazia. Use OVERWRITE=True ou outro destino.")
        shutil.rmtree(DEST_DIR)
    DEST_DIR.mkdir(parents=True, exist_ok=True)


def ler_label(txt_path):
    """Lê o .txt original e devolve um dict {chave: valor}."""
    dados = {}
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        for linha in f:
            if ":" in linha:
                chave, valor = linha.split(":", 1)
                dados[chave.strip().lower()] = valor.strip()
    return dados


def parse_corners(texto):
    """'558,438 687,439 687,482 558,481' -> [(558,438), (687,439), (687,482), (558,481)]"""
    pontos = []
    for par in texto.split():
        x, y = par.split(",")
        pontos.append((float(x), float(y)))
    if len(pontos) != 4:
        raise ValueError(f"esperava 4 cantos, encontrei {len(pontos)}")
    return pontos


def corners_para_yolo(pontos, largura, altura):
    """Converte os 4 cantos em bbox YOLO normalizada (xc, yc, w, h)."""
    xs = [p[0] for p in pontos]
    ys = [p[1] for p in pontos]

    # Caixa que envolve os 4 cantos + margem, limitada às bordas da imagem.
    xmin = max(0.0, min(xs) - BBOX_PADDING_PX)
    xmax = min(float(largura), max(xs) + BBOX_PADDING_PX)
    ymin = max(0.0, min(ys) - BBOX_PADDING_PX)
    ymax = min(float(altura), max(ys) + BBOX_PADDING_PX)

    w, h = xmax - xmin, ymax - ymin
    if w <= 1 or h <= 1:
        raise ValueError("bounding box degenerada")

    xc, yc = (xmin + xmax) / 2, (ymin + ymax) / 2
    return xc / largura, yc / altura, w / largura, h / altura


def nome_unico(img_path):
    """Prefixa o nome com o caminho relativo para evitar colisão entre pastas diferentes."""
    rel = img_path.relative_to(SOURCE_DIR)
    partes = list(rel.parent.parts) + [img_path.stem]
    return re.sub(r"[^\w\-.]", "_", "_".join(partes))


def coletar_amostras():
    """Encontra pares imagem + .txt e converte os labels (ainda em memória)."""
    amostras, ignoradas = [], 0
    imagens = sorted(p for p in SOURCE_DIR.rglob("*") if p.suffix.lower() in IMAGE_EXTS)
    print(f"Imagens encontradas: {len(imagens)}")

    for img in imagens:
        txt = img.with_suffix(".txt")
        if not txt.exists():
            ignoradas += 1
            continue
        try:
            dados = ler_label(txt)
            pontos = parse_corners(dados["corners"])
            with Image.open(img) as im:  # só lê o cabeçalho, é rápido
                largura, altura = im.size
            xc, yc, w, h = corners_para_yolo(pontos, largura, altura)
        except Exception as e:
            print(f"[AVISO] Ignorando {img.name}: {e}")
            ignoradas += 1
            continue

        amostras.append({
            "img": img,
            "nome": nome_unico(img),
            "grupo": str(img.parent),
            "linha_yolo": f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n",
            "placa": dados.get("plate", ""),
            "tipo": dados.get("type", ""),
        })

    print(f"Amostras válidas: {len(amostras)} | ignoradas: {ignoradas}")
    if not amostras:
        sys.exit("[ERRO] Nenhuma amostra válida encontrada.")
    return amostras


def dividir(amostras):
    """Sorteia as amostras entre train/val/test de forma reprodutível (SEED)."""
    rng = random.Random(SEED)

    # Define as "unidades" de sorteio: pastas inteiras ou imagens individuais.
    grupos = {}
    for a in amostras:
        grupos.setdefault(a["grupo"], []).append(a)

    if GROUP_BY_FOLDER and len(grupos) > 1:
        unidades = [grupos[k] for k in sorted(grupos)]  # sorted -> ordem determinística antes do shuffle
        print(f"Split por pasta/grupo: {len(unidades)} grupos.")
    else:
        if GROUP_BY_FOLDER:
            print("[AVISO] Só há 1 pasta; usando sorteio por imagem.")
        else:
            print("Split por imagem (sem agrupar).")
        unidades = [[a] for a in amostras]

    rng.shuffle(unidades)

    total = len(amostras)
    ativos = [(n, r) for n, r in
              [("train", TRAIN_RATIO), ("val", VAL_RATIO), ("test", TEST_RATIO)] if r > 0]

    # Metas acumuladas de quantidade por split; o último split ativo recebe o restante.
    metas, acumulado = {}, 0.0
    for nome, ratio in ativos:
        acumulado += ratio
        metas[nome] = round(total * acumulado)

    resultado = {n: [] for n, _ in ativos}
    contagem = 0
    for unidade in unidades:
        for nome, _ in ativos:
            if contagem < metas[nome] or nome == ativos[-1][0]:
                resultado[nome].extend(unidade)
                break
        contagem += len(unidade)

    return resultado


def escrever_dataset(splits):
    """Copia imagens e grava os labels convertidos + manifest + data.yaml."""
    manifest = []
    for split, itens in splits.items():
        pasta_img = DEST_DIR / "images" / split
        pasta_lbl = DEST_DIR / "labels" / split
        pasta_img.mkdir(parents=True, exist_ok=True)
        pasta_lbl.mkdir(parents=True, exist_ok=True)

        for i, a in enumerate(itens, 1):
            destino_img = pasta_img / f"{a['nome']}{a['img'].suffix.lower()}"
            shutil.copy2(a["img"], destino_img)                 # cópia: origem intacta
            (pasta_lbl / f"{a['nome']}.txt").write_text(a["linha_yolo"])
            manifest.append([split, destino_img.name, a["placa"], a["tipo"], a["grupo"]])
            if i % 1000 == 0:
                print(f"  [{split}] {i}/{len(itens)}")
        print(f"[{split}] {len(itens)} imagens.")

    # data.yaml
    linhas = [f"path: {DEST_DIR.resolve().as_posix()}", "train: images/train"]
    linhas.append("val: images/val" if "val" in splits else "val: images/train")
    if "test" in splits:
        linhas.append("test: images/test")
    linhas.append(f"nc: {len(CLASS_NAMES)}")
    linhas.append("names:")
    linhas += [f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES)]
    (DEST_DIR / "data.yaml").write_text("\n".join(linhas) + "\n")

    # Manifest para auditoria/reprodutibilidade
    with open(DEST_DIR / "split_manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["split", "arquivo", "placa", "tipo", "pasta_origem"])
        w.writerows(manifest)


def main():
    validar_configuracao()
    preparar_destino()
    amostras = coletar_amostras()
    splits = dividir(amostras)
    escrever_dataset(splits)
    print(f"\nConcluído! Dataset YOLO em: {DEST_DIR.resolve()}")
    print(f"Use este arquivo no treino: {(DEST_DIR / 'data.yaml').resolve()}")


if __name__ == "__main__":
    main()