# PIPELINE RESUMIDO 
 
Sistema de **reconhecimento automático de placas** para controle de acesso em catracas. A câmera captura o veículo, o sistema localiza a placa, lê os caracteres e valida o formato (Mercosul ou antiga) para liberar ou não o acesso.
 
**Fluxo:** imagem → detecção da placa (YOLO) → OCR → validação (máscara + regex) → resultado (mosaico + CSV)

## Modelos de detecção (YOLO)
 
O repositório contém dois modelos:
 
| Modelo | Classes |
|---|---|
| **2 classes** | Placa de 1 linha e placa de 2 linhas |
| **4 classes** | Mercosul 1 linha, Mercosul 2 linhas, Brasil (antiga) 1 linha, Brasil (antiga) 2 linhas |
 
O modelo de 4 classes já informa o tipo da placa, o que permite aplicar uma validação mais rígida no texto lido.

## Motores de OCR testados
 
- **EasyOCR:** OCR genérico, simples e estável. Usado em `aplicarModelo-PastaImgs.py`.
- **PaddleOCR:** OCR genérico e rápido, mas sensível ao ambiente (ver workaround no script). Usado em `aplicarModelo-PastaImgs.py`.
- **Fast-ALPR:** biblioteca especializada em placas, com detector e OCR próprios. Foi o melhor modelo testaado até então e está aplicado nos scripts v4 e v5.

## Arquivos
 
| Arquivo | Função |
|---|---|
| `converterDataset-YOLO.py` | Converte o dataset RodoSol/UFPR-ALPR para o formato YOLO (split train/val/test) |
| `aplicarModelo-PastaImgs.py` | YOLO + OCR (EasyOCR ou PaddleOCR) em uma pasta de imagens |
| `aplicacaoModelov4-fastalpr.py` | Fast-ALPR completo (detector + OCR) |
| `aplicacaoModelov5-fast_rodosol.py` | YOLO próprio (4 classes) + OCR do Fast-ALPR |
| `comprovacaoResultados.py` | Gera gráficos de desempenho a partir do `resultados.csv` |
 

## Como usar
 
```bash
pip install ultralytics opencv-python numpy pillow pandas matplotlib seaborn
pip install easyocr                  # opcional
pip install paddlepaddle paddleocr   # opcional
pip install fast-alpr                # opcional
```
 
1. Edite as **variáveis globais** no topo do script (pasta de imagens, pasta de saída, caminho do modelo `.pt`).
2. Execute o script desejado, por exemplo: `python aplicacaoModelov5-fast_rodosol.py`
3. Para gerar os gráficos, aponte `CAMINHO_CSV` em `comprovacaoResultados.py` e execute-o.
## Saídas
 
- `<imagem>_mosaico.jpg`: imagem com a detecção, o recorte da placa e o texto lido
- `resultados.csv`: imagem, placa lida, tipo, confiança da detecção e confiança do OCR
- `dashboard_sprint.png`: gráficos de desempenho
## Observações
 
- Os scripts têm caminhos absolutos do ambiente de desenvolvimento; ajuste antes de rodar.
- No v5, as classes do YOLO precisam conter `mercosul` ou `antiga`/`cinza` no nome.
- O conversor gera apenas a classe `plate`; para treinar os modelos de 2 ou 4 classes é preciso ajustar `CLASS_NAMES`.
 
