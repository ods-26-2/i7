"""
Comprovação de ACERTOS: quantas placas lidas pelo pipeline estavam realmente CORRETAS.

Diferença para o comprovacaoResultados.py: aquele mede quantas leituras passaram na validação
de formato (regex); este compara a placa lida com a placa VERDADEIRA do dataset
(coluna `placa` do split_manifest.csv, gerado pelo converterDataset-YOLO.py).

Como funciona
-------------
Para cada pasta de saída do v5 (saidas/car-br, saidas/moto-me, ...):
    - lê o `resultados.csv` do v5;
    - lê o `split_manifest.csv` da categoria correspondente (datasetConvertidoYOLO/<categoria>/);
    - considera como "processadas" as imagens que têm um `<nome>_mosaico.jpg` na pasta de saída
      (o v5 grava um mosaico para toda imagem processada, inclusive as sem detecção, e por isso
      o script funciona também com --limite no v5);
    - classifica cada imagem em: correta | errada aceita | errada rejeitada | sem leitura |
      sem detecção.

    "errada aceita" é o caso mais grave: o pipeline leu errado E a validação de formato aprovou.

Saídas (na pasta de saída informada):
    dashboard_acertos.png     painel com 4 gráficos
    acertos_detalhado.csv     uma linha por imagem (placa real x lida), para revisar os erros

Uso:
    python scripts/comprovacaoAcertos.py                         # usa PASTA_SAIDA e PASTA_DATASET do .env
    python scripts/comprovacaoAcertos.py --saida saidas/teste    # outra pasta de saída
    python scripts/comprovacaoAcertos.py --saida saidas --manifest datasetConvertidoYOLO/car-br/split_manifest.csv
                                          # quando o v5 foi rodado com UMA só pasta (resultados.csv direto em saidas/)
    Acrescente --mostrar para abrir a janela do gráfico.

Comparar experimentos (o v5 rodado várias vezes com configurações diferentes, cada uma em sua pasta):
    python scripts/comprovacaoAcertos.py --comparar saidas/exp00_base saidas/exp01_sem_prep saidas/exp02_dobrar
    A PRIMEIRA pasta é a referência. A comparação é PAREADA (mesmas imagens): "(+a/-b)" mostra
    quantas imagens passaram a acertar (+a) e quantas passaram a errar (-b) em relação à referência.

O nome das imagens no resultados.csv precisa ser o mesmo do manifest: rode o v5 sobre as pastas
`images/test` do dataset convertido.
"""
import argparse
import os
import sys
from pathlib import Path

if "--mostrar" not in sys.argv:  # sem janela (servidor, WSL sem interface): backend que só grava arquivo
    import matplotlib
    matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from dotenv import load_dotenv

# =========================== VARIÁVEIS GLOBAIS ===========================
RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

PASTA_SAIDA = RAIZ / os.getenv("PASTA_SAIDA", "saidas")
PASTA_DATASET = RAIZ / os.getenv("PASTA_DATASET", "datasetConvertidoYOLO")

# Marcas que o v5 escreve em `tipo_detectado` quando a validação de formato NÃO aprovou a leitura.
MARCAS_REJEICAO = ("Inválida", "Sem Leitura", "Desconhecido")

ORDEM = ["correta", "errada aceita", "errada rejeitada", "sem leitura", "sem detecção"]
CORES = {"correta": "#2ca02c", "errada aceita": "#d62728", "errada rejeitada": "#ff9f1c",
         "sem leitura": "#9e9e9e", "sem detecção": "#4d4d4d"}

sns.set_theme(style="whitegrid")
plt.rcParams.update({"font.sans-serif": "DejaVu Sans", "font.size": 10})
# =========================================================================


def limpar_placa(texto) -> str:
    """Maiúsculas, só letras e dígitos (mesma limpeza do pipeline)."""
    return "".join(c for c in str(texto).upper() if c.isascii() and c.isalnum())


def chave(nome) -> str:
    """Nome do arquivo sem extensão, em minúsculas: chave para casar resultados e manifest."""
    return Path(str(nome)).stem.lower()


# ------------------------------ LEITURA ------------------------------------
def carregar_manifest(caminho: Path) -> pd.DataFrame:
    df = pd.read_csv(caminho, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    faltando = [c for c in ("arquivo", "placa") if c not in df.columns]
    if faltando:
        sys.exit(f"[ERRO] {caminho} não tem a(s) coluna(s) {faltando}. Colunas: {list(df.columns)}")
    out = pd.DataFrame({
        "chave": df["arquivo"].map(chave),
        "placa_real": df["placa"].map(limpar_placa),
        "tipo_veiculo": df["tipo"] if "tipo" in df.columns else "desconhecido",
    })
    return out[out["placa_real"] != ""].drop_duplicates("chave")


def imagens_processadas(pasta: Path) -> set:
    """Imagens que o v5 processou nesta pasta de saída (têm um *_mosaico.jpg)."""
    sufixo = "_mosaico"
    return {p.stem[:-len(sufixo)].lower() for p in pasta.glob("*_mosaico.jpg") if p.stem.endswith(sufixo)}


def carregar_resultados(caminho: Path) -> pd.DataFrame:
    df = pd.read_csv(caminho, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    obrigatorias = ["imagem", "placa_lida", "tipo_detectado", "conf_det", "conf_ocr"]
    faltando = [c for c in obrigatorias if c not in df.columns]
    if faltando:
        sys.exit(f"[ERRO] {caminho} não tem a(s) coluna(s) {faltando}. É o resultados.csv do v5?")
    df["chave"] = df["imagem"].map(chave)
    df["conf_det"] = pd.to_numeric(df["conf_det"], errors="coerce")
    df["conf_ocr"] = pd.to_numeric(df["conf_ocr"], errors="coerce")
    # RodoSol tem 1 veículo por imagem: fica a detecção mais confiante de cada imagem.
    return df.sort_values("conf_det", ascending=False).drop_duplicates("chave")


# ------------------------------ AVALIAÇÃO ----------------------------------
def classificar(linha) -> str:
    if not linha["detectada"]:
        return "sem detecção"
    if linha["placa_lida"] == "":
        return "sem leitura"
    if linha["placa_lida"] == linha["placa_real"]:
        return "correta"
    return "errada aceita" if linha["aceita"] else "errada rejeitada"


def erros_por_posicao(real: str, lida: str) -> list:
    """1 se o caractere da posição está errado (ou faltando) na placa lida, senão 0."""
    return [int(i >= len(lida) or lida[i] != c) for i, c in enumerate(real)]


def avaliar_pasta(csv_resultados: Path, csv_manifest: Path, categoria: str) -> pd.DataFrame:
    res = carregar_resultados(csv_resultados)
    manifest = carregar_manifest(csv_manifest)

    processadas = imagens_processadas(csv_resultados.parent)
    if not processadas:
        print(f"[AVISO] {categoria}: nenhum *_mosaico.jpg em {csv_resultados.parent}; "
              f"usando só as imagens do resultados.csv (imagens sem detecção NÃO serão contadas).")
        processadas = set(res["chave"])

    sem_gabarito = processadas - set(manifest["chave"])
    if sem_gabarito:
        print(f"[AVISO] {categoria}: {len(sem_gabarito)} imagem(ns) processada(s) não existem no manifest "
              f"e foram ignoradas (ex.: {sorted(sem_gabarito)[:3]}). Rodou o v5 sobre images/test?")

    base = manifest[manifest["chave"].isin(processadas)]
    df = base.merge(res, on="chave", how="left")
    df["detectada"] = df["imagem"].notna()
    df["placa_lida"] = df["placa_lida"].fillna("").map(limpar_placa)
    df["tipo_detectado"] = df["tipo_detectado"].fillna("")
    df["aceita"] = (df["placa_lida"] != "") & ~df["tipo_detectado"].apply(
        lambda t: any(m in t for m in MARCAS_REJEICAO))
    df["resultado"] = df.apply(classificar, axis=1)
    df["categoria"] = categoria
    # Colunas novas do v5 (texto bruto do OCR e confiança mínima) entram no detalhe, se existirem.
    colunas = ["categoria", "chave", "tipo_veiculo", "placa_real", "placa_lida"]
    if "texto_ocr_bruto" in df.columns:
        df["texto_ocr_bruto"] = df["texto_ocr_bruto"].fillna("")
        colunas.append("texto_ocr_bruto")
    colunas += ["resultado", "aceita", "tipo_detectado", "conf_det", "conf_ocr"]
    if "conf_ocr_min" in df.columns:
        colunas.append("conf_ocr_min")
    return df[colunas].rename(columns={"chave": "imagem"})


# ------------------------------ RELATÓRIO ----------------------------------
def tabela_por_categoria(df: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for cat, g in df.groupby("categoria", sort=True):
        n = len(g)
        c = g["resultado"].value_counts()
        linhas.append({
            "categoria": cat, "imagens": n, "corretas": int(c.get("correta", 0)),
            "pct_corretas": round(100 * c.get("correta", 0) / n, 2),
            "erradas_aceitas": int(c.get("errada aceita", 0)),
            "sem_deteccao": int(c.get("sem detecção", 0)),
        })
    return pd.DataFrame(linhas)


def imprimir_resumo(df: pd.DataFrame):
    n = len(df)
    c = df["resultado"].value_counts().reindex(ORDEM, fill_value=0)
    lidas = int(c[["correta", "errada aceita", "errada rejeitada"]].sum())
    detectadas = int((df["resultado"] != "sem detecção").sum())

    def pct(a, b):
        return f"{100 * a / b:6.2f}%" if b else "   n/a "

    print("\n" + "=" * 64)
    print(f" ACERTOS DO PIPELINE  ({n} imagens processadas)")
    print("=" * 64)
    print(f" Placas CORRETAS ................ {int(c['correta']):>6}  {pct(c['correta'], n)}  das imagens")
    print(f" Corretas entre as lidas ........ {int(c['correta']):>6}  {pct(c['correta'], lidas)}  de {lidas} lidas")
    print(f" Detectadas ..................... {detectadas:>6}  {pct(detectadas, n)}")
    print("-" * 64)
    for nome in ORDEM:
        print(f"   {nome:<18} {int(c[nome]):>6}  {pct(c[nome], n)}")
    aceitas = int(c["correta"] + c["errada aceita"])
    print("-" * 64)
    print(f" Das {aceitas} leituras que passaram na validação de formato, "
          f"{int(c['errada aceita'])} estavam ERRADAS ({pct(c['errada aceita'], aceitas).strip()}).")
    print("-" * 64)
    print(tabela_por_categoria(df).to_string(index=False))
    print("=" * 64)


def gerar_dashboard(df: pd.DataFrame, destino: Path, mostrar: bool):
    c = df["resultado"].value_counts().reindex(ORDEM, fill_value=0)
    lidas = df[df["resultado"].isin(["correta", "errada aceita", "errada rejeitada"])]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Relatório de Acertos - Pipeline ALPR (placa lida x placa verdadeira)",
                 fontsize=16, fontweight="bold", y=1.0)

    # 1) Rosca com o resultado de cada imagem
    ax = axes[0, 0]
    presentes = [r for r in ORDEM if c[r] > 0]
    ax.pie([c[r] for r in presentes], labels=[f"{r} ({int(c[r])})" for r in presentes],
           colors=[CORES[r] for r in presentes], autopct="%1.1f%%", startangle=90, pctdistance=0.8,
           wedgeprops=dict(width=0.4, edgecolor="w"), textprops=dict(fontsize=9))
    ax.set_title("Resultado por imagem", fontsize=12, fontweight="bold")

    # 2) % de corretas por categoria
    ax = axes[0, 1]
    cat = tabela_por_categoria(df)
    sns.barplot(data=cat, x="categoria", y="pct_corretas", color=CORES["correta"], ax=ax)
    for i, r in cat.iterrows():
        ax.text(i, r["pct_corretas"] + 1, f"{r['pct_corretas']:.1f}%\n(n={r['imagens']})", ha="center", fontsize=9)
    ax.set_ylim(0, 115)
    ax.set_xlabel("")
    ax.set_ylabel("% de placas corretas")
    ax.set_title("Acerto por categoria", fontsize=12, fontweight="bold")

    # 3) Erro por posição do caractere (entre as placas lidas)
    ax = axes[1, 0]
    if len(lidas):
        tam = int(lidas["placa_real"].str.len().max())
        erros = pd.DataFrame([erros_por_posicao(r, l) + [0] * (tam - len(r))
                              for r, l in zip(lidas["placa_real"], lidas["placa_lida"])])
        taxa = 100 * erros.mean()
        sns.barplot(x=[str(i + 1) for i in range(tam)], y=taxa.values, color=CORES["errada aceita"], ax=ax)
        for i, v in enumerate(taxa.values):
            ax.text(i, v + 0.5, f"{v:.1f}%", ha="center", fontsize=9)
        ax.set_ylim(0, max(10, taxa.max() * 1.25))
        ax.set_xlabel("posição do caractere na placa")
        ax.set_ylabel("% de leituras com erro nesta posição")
    else:
        ax.text(0.5, 0.5, "sem leituras", ha="center", va="center")
    ax.set_title(f"Erros por posição ({len(lidas)} placas lidas)", fontsize=12, fontweight="bold")

    # 4) Confiança do OCR: leituras corretas x erradas
    ax = axes[1, 1]
    if len(lidas):
        dados = lidas.assign(grupo=lidas["resultado"].map(lambda r: "correta" if r == "correta" else "errada"))
        sns.histplot(data=dados, x="conf_ocr", hue="grupo", hue_order=["correta", "errada"],
                     palette={"correta": CORES["correta"], "errada": CORES["errada aceita"]},
                     bins=20, binrange=(0, 1), multiple="layer", alpha=0.6, ax=ax)
        ax.set_xlim(0, 1)
    else:
        ax.text(0.5, 0.5, "sem leituras", ha="center", va="center")
    ax.set_xlabel("Confiança média do OCR")
    ax.set_ylabel("nº de placas")
    ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True))  # contagem: só números inteiros
    ax.set_title("Confiança do OCR: corretas x erradas", fontsize=12, fontweight="bold")

    plt.tight_layout()
    plt.savefig(destino, dpi=300, bbox_inches="tight")
    print(f"Dashboard salvo em: {destino}")
    if mostrar:
        plt.show()
    plt.close(fig)


# ------------------------------ COMPARAÇÃO ---------------------------------
def comparar_experimentos(dfs: dict) -> tuple:
    """
    Compara experimentos (o PRIMEIRO é a referência) nas MESMAS imagens.
    Devolve (tabela de % corretas, tabela de 'erradas aceitas', imagens comparadas).
    Nas colunas dos demais experimentos, "(+a/-b)" = a imagens que a referência errava e agora
    acertam, b imagens que a referência acertava e agora erram (comparação pareada).
    """
    nomes = list(dfs)
    chaves = None
    for df in dfs.values():
        k = set(zip(df["categoria"], df["imagem"]))
        chaves = k if chaves is None else chaves & k
    if not chaves:
        sys.exit("[ERRO] Os experimentos não têm nenhuma imagem em comum.")

    base_df = {}
    for nome, df in dfs.items():
        d = df[[(c, i) in chaves for c, i in zip(df["categoria"], df["imagem"])]].copy()
        d["correta"] = d["resultado"] == "correta"
        base_df[nome] = d.set_index(["categoria", "imagem"]).sort_index()

    ref = base_df[nomes[0]]
    categorias = sorted(ref.index.get_level_values(0).unique()) + ["TOTAL"]
    linhas_pct, linhas_err = [], []
    for cat in categorias:
        lp, le = {"categoria": cat}, {"categoria": cat}
        for nome in nomes:
            d = base_df[nome]
            sel = slice(None) if cat == "TOTAL" else d.index.get_level_values(0) == cat
            dd = d if cat == "TOTAL" else d[sel]
            rr = ref if cat == "TOTAL" else ref[ref.index.get_level_values(0) == cat]
            pct = 100 * dd["correta"].mean()
            texto = f"{pct:.1f}%"
            if nome != nomes[0]:
                ganhos = int((dd["correta"] & ~rr["correta"].reindex(dd.index)).sum())
                perdas = int((~dd["correta"] & rr["correta"].reindex(dd.index)).sum())
                texto += f" (+{ganhos}/-{perdas})"
            lp[nome] = texto
            le[nome] = int((dd["resultado"] == "errada aceita").sum())
        linhas_pct.append(lp)
        linhas_err.append(le)
    return pd.DataFrame(linhas_pct), pd.DataFrame(linhas_err), len(chaves)


def avaliar_saida(saida: Path, dataset: Path, manifest_unico: Path | None):
    """Avalia todos os resultados.csv de uma pasta de saída. Devolve o DataFrame (ou None)."""
    csvs = sorted(saida.rglob("resultados.csv")) if saida.is_dir() else []
    if not csvs:
        print(f"[AVISO] Nenhum resultados.csv encontrado em {saida}.")
        return None

    partes = []
    for csv_res in csvs:
        if manifest_unico and len(csvs) == 1:
            manifest, categoria = manifest_unico, manifest_unico.parent.name
        elif csv_res.parent == saida:
            print(f"[AVISO] {csv_res} está direto na pasta de saída: informe o manifest com --manifest. Ignorado.")
            continue
        else:
            categoria = csv_res.parent.name
            manifest = dataset / categoria / "split_manifest.csv"
        if not manifest.is_file():
            print(f"[AVISO] {categoria}: manifest não encontrado em {manifest}. Ignorado.")
            continue
        partes.append(avaliar_pasta(csv_res, manifest, categoria))

    if not partes:
        print(f"[AVISO] Nada para avaliar em {saida} (confira --dataset e --manifest).")
        return None
    df = pd.concat(partes, ignore_index=True)
    return df if not df.empty else None


# ------------------------------- MAIN --------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Quantas placas lidas pelo pipeline estavam corretas.")
    parser.add_argument("--saida", default=None, help="pasta de saída do v5 (padrão: PASTA_SAIDA do .env)")
    parser.add_argument("--dataset", default=None, help="raiz do dataset convertido (padrão: PASTA_DATASET do .env)")
    parser.add_argument("--manifest", default=None,
                        help="split_manifest.csv a usar quando há um único resultados.csv (v5 rodado com uma pasta só)")
    parser.add_argument("--comparar", nargs="+", metavar="PASTA_SAIDA",
                        help="compara experimentos: 2 ou mais pastas de saída do v5; a PRIMEIRA é a referência")
    parser.add_argument("--mostrar", action="store_true", help="abre a janela do gráfico")
    args = parser.parse_args()

    dataset = RAIZ / args.dataset if args.dataset else PASTA_DATASET
    manifest_unico = RAIZ / args.manifest if args.manifest else None

    # ---- modo comparação ----
    if args.comparar:
        if len(args.comparar) < 2:
            sys.exit("[ERRO] --comparar precisa de pelo menos 2 pastas de saída (a primeira é a referência).")
        dfs = {}
        for p in args.comparar:
            pasta = RAIZ / p
            df = avaliar_saida(pasta, dataset, manifest_unico)
            if df is None:
                sys.exit(f"[ERRO] Sem resultados avaliáveis em {pasta}.")
            nome = pasta.name
            while nome in dfs:
                nome += "_"
            dfs[nome] = df
        tab_pct, tab_err, n = comparar_experimentos(dfs)
        nomes = list(dfs)
        print("\n" + "=" * 72)
        print(f" COMPARAÇÃO DE EXPERIMENTOS ({n} imagens em comum; referência: {nomes[0]})")
        print("=" * 72)
        print(" % de placas CORRETAS   (+a/-b = imagens corrigidas / imagens quebradas vs. referência)")
        print(tab_pct.to_string(index=False))
        print("\n Leituras ERRADAS que passaram na validação de formato (nº de imagens)")
        print(tab_err.to_string(index=False))
        print("=" * 72)
        saida_csv = (RAIZ / args.comparar[0]).parent / "comparacao_experimentos.csv"
        tab_pct.merge(tab_err.rename(columns={nm: f"{nm} [erradas aceitas]" for nm in nomes}),
                      on="categoria").to_csv(saida_csv, index=False, encoding="utf-8")
        print(f"Comparação salva em: {saida_csv}")
        return

    # ---- modo normal ----
    saida = RAIZ / args.saida if args.saida else PASTA_SAIDA
    if not saida.is_dir() or not any(saida.rglob("resultados.csv")):
        sys.exit(f"[ERRO] Nenhum resultados.csv encontrado em {saida}. Rode o v5 antes.")
    df = avaliar_saida(saida, dataset, manifest_unico)
    if df is None:
        sys.exit("[ERRO] Nada para avaliar (confira --saida, --dataset e --manifest).")

    imprimir_resumo(df)
    df.to_csv(saida / "acertos_detalhado.csv", index=False, encoding="utf-8")
    print(f"Detalhe por imagem salvo em: {saida / 'acertos_detalhado.csv'}")
    gerar_dashboard(df, saida / "dashboard_acertos.png", args.mostrar)


if __name__ == "__main__":
    main()