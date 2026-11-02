import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# Configuração de estilo visual
sns.set_theme(style="whitegrid")
plt.rcParams.update({'font.sans-serif': 'DejaVu Sans', 'font.size': 10})

# Caminho para o seu CSV gerado na sprint
CAMINHO_CSV = "/home/gabriel/Desktop/ODS/saida_mosaicos/aplicacaoModelov5-fast+rodosol/resultados.csv"

def gerar_relatorio_sprint(caminho_csv):
    df = pd.read_csv(caminho_csv)
    
    # Criar figura com subplots
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle('Relatório de Desempenho da Sprint - Pipeline ALPR', fontsize=16, fontweight='bold', y=1.02)

    # -------------------------------------------------------------------------
    # Gráfico 1: Confiança da Detecção (YOLO) vs. Confiança do OCR (FastALPR)
    # -------------------------------------------------------------------------
    ax1 = axes[0]
    sns.scatterplot(
        data=df, 
        x="conf_det", 
        y="conf_ocr", 
        hue="tipo_detectado", 
        style="tipo_detectado",
        s=100, 
        palette="viridis", 
        ax=ax1
    )
    ax1.set_title("Nível de Confiança por Detecção (YOLO vs. OCR)", fontsize=12, fontweight='bold')
    ax1.set_xlabel("Confiança da Detecção YOLO", fontsize=10)
    ax1.set_ylabel("Confiança Média do OCR", fontsize=10)
    ax1.set_xlim(0, 1.05)
    ax1.set_ylim(0, 1.05)
    
    # Linhas de referência de qualidade (ex: 75%)
    ax1.axhline(0.75, color='red', linestyle='--', alpha=0.5, label='Threshold Qualidade OCR (75%)')
    ax1.axvline(0.75, color='blue', linestyle='--', alpha=0.5, label='Threshold Detecção (75%)')
    ax1.legend(loc='lower left', fontsize=8)

    # -------------------------------------------------------------------------
    # Gráfico 2: Proporção de Placas Válidas x Inválidas / Tipos Detectados
    # -------------------------------------------------------------------------
    ax2 = axes[1]
    
    # Classificar o status para o gráfico
    def categorizar_status(tipo):
        if "Inválida" in str(tipo):
            return "Inválida / Ruído"
        elif "Sem Leitura" in str(tipo):
            return "Sem Leitura"
        return "Válida (" + str(tipo).split(" ")[0] + ")"

    df['status_simplificado'] = df['tipo_detectado'].apply(categorizar_status)
    contagem = df['status_simplificado'].value_counts()

    cores = sns.color_palette("Set2", len(contagem))
    wedges, texts, autotexts = ax2.pie(
        contagem, 
        labels=contagem.index, 
        autopct='%1.1f%%', 
        startangle=140, 
        colors=cores,
        wedgeprops=dict(width=0.4, edgecolor='w') # Formato de Rosca
    )
    plt.setp(autotexts, size=10, weight="bold")
    ax2.set_title("Taxa de Sucesso na Validação das Placas", fontsize=12, fontweight='bold')

    # Ajustes finais
    plt.tight_layout()
    caminho_saida = Path(caminho_csv).parent / "dashboard_sprint.png"
    plt.savefig(caminho_saida, dpi=300, bbox_inches='tight')
    print(f"Dashboard salvo em: {caminho_saida}")
    plt.show()

if __name__ == "__main__":
    gerar_relatorio_sprint(CAMINHO_CSV)