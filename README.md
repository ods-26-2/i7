# ParkFlow · I7: leitura de placas (detecção + OCR)

> ## ⚠️ LEIA PRIMEIRO: [`MUDANCAS_VER5.md`](MUDANCAS_VER5.md)
> Existe um segundo documento, curto, com o que a nossa squad **mudou em relação à arquitetura 6c** (escopo, cancela simulada, diferenças no payload do I7) e **como usar os arquivos**. Abra-o antes de rodar qualquer coisa.


Componente de **reconhecimento automático de placas** para controle de acesso veicular. A câmera captura o veículo, o sistema localiza a placa, lê os caracteres, valida o formato (Mercosul ou antiga) e entrega a leitura, com a confiança, ao restante do sistema (A8) pelo evento `ods.i7.plate_read`, publicado no Kafka.

## Fluxo

```
imagem → YOLO (4 classes) → recorte (+ margem nas Mercosul) → OCR (cct-s-v2-global)
       → correção posicional + regex → resultado
                                         ├─ mosaico + resultados.csv
                                         └─ evento ods.i7.plate_read
                                              ├─ modo arquivo: eventos.jsonl (sem rede)
                                              └─ modo kafka:   tópico ods.i7.plate_read no broker
```

## Início rápido (Docker)

O componente roda em um **container Docker puro** (sem Dev Containers), que isola as bibliotecas e as chamadas de SO. É o jeito recomendado de executar e o que outras equipes devem usar.

Requisitos: Docker Engine ou Docker Desktop instalado e **rodando** (os computadores do lab já vêm configurados) e a pasta `datasetConvertidoYOLO/` na raiz do projeto (ela não vai para o Git; veja `converterDataset-YOLO.py`).

```bash
cp .env.example .env     # ajuste os valores (sem aspas, caminhos relativos)
./run.sh build           # constrói a imagem (só na primeira vez ou quando o código/requirements mudar)
./run.sh                 # roda o pipeline com 20 imagens por pasta (teste rápido)
```

Para testar só a imagem, sem dataset nem rede:

```bash
./run.sh scripts/publicador_eventos.py --exemplo
```

Detalhes na seção [Execução com Docker](#execução-com-docker).

## Resultados da configuração atual

Medidos com a placa verdadeira do dataset (`placa` do `split_manifest.csv`), em **800 imagens de teste** do RodoSol (200 por categoria):

| Categoria | Placas lidas corretamente |
|---|---|
| Carro, placa antiga | 95,5% |
| Carro, Mercosul | 97,0% |
| Moto, placa antiga | 72,0% |
| Moto, Mercosul | 71,5% |
| **Total** | **84,0%** (IC 95%: ~81% a 86%) |

- Detecção da placa: 100%. Tipo de placa (4 classes) correto: 800 de 800.
- **Regra de liberação sugerida:** confiança **mínima** por caractere ≥ 0,9. Libera 73,8% das placas e 1,5% das liberadas estão erradas (IC 95%: 0,8% a 2,9%). Carros: libera 88,5%, com 0,8% de erradas. Motos: libera 59%, com 2,5% de erradas. É esse valor que o evento leva em `payload.confidence` (veja "Publicação de eventos").
- A confiança **média** é pior que a mínima como critério (com média ≥ 0,95, 5,4% das liberadas estão erradas).
- Limitação: o RodoSol tem câmera fixa e condições controladas. Chuva, noite e outros ângulos ainda não foram testados.

## Modelos

**Detecção (YOLO):** `modelos/modelo4classesV1/best.pt` (4 classes: `placa_1L_BR`, `placa_1L_ME`, `placa_2L_BR`, `placa_2L_ME`; BR = antiga, ME = Mercosul). O diretório também tem `modelos/modelo2classes/best.pt` (1 linha / 2 linhas), que o pipeline atual não usa.

**OCR:** `cct-s-v2-global-model`, da biblioteca [fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr) (usada pelo Fast-ALPR). O modelo é baixado na primeira execução. Com Docker, ele fica guardado no volume `i7-ocr-cache` e não é baixado de novo.

Se a sua rede bloquear o download (erro `CERTIFICATE_VERIFY_FAILED`), baixe os dois arquivos à mão:

```powershell
# Windows (PowerShell)
$base = "https://github.com/ankandrew/cnn-ocr-lp/releases/download/arg-plates"
$d = "$HOME\.cache\fast-plate-ocr\cct-s-v2-global-model"; New-Item -ItemType Directory -Force $d
Invoke-WebRequest "$base/cct_s_v2_global.onnx" -OutFile "$d\cct_s_v2_global.onnx"
Invoke-WebRequest "$base/cct_s_v2_global_plate_config.yaml" -OutFile "$d\cct_s_v2_global_plate_config.yaml"
```

```bash
# Linux / macOS
base=https://github.com/ankandrew/cnn-ocr-lp/releases/download/arg-plates
d=~/.cache/fast-plate-ocr/cct-s-v2-global-model; mkdir -p "$d"
curl -L -o "$d/cct_s_v2_global.onnx" "$base/cct_s_v2_global.onnx"
curl -L -o "$d/cct_s_v2_global_plate_config.yaml" "$base/cct_s_v2_global_plate_config.yaml"
```

Para usar esses arquivos no Docker, copie-os para o volume do cache (Linux / macOS; a imagem precisa já estar construída):

```bash
docker run --rm -v i7-ocr-cache:/destino -v "$HOME/.cache/fast-plate-ocr:/origem" \
  --entrypoint cp parkflow-i7 -r /origem/. /destino/
```

## Arquivos (`scripts/`)

| Arquivo | Função |
|---|---|
| `aplicacaoModelov5-fast+rodosol.py` | **Pipeline atual:** YOLO de 4 classes + OCR + validação, com mosaicos, CSV e publicação de eventos |
| `publicador_eventos.py` | Monta e valida o evento `ods.i7.plate_read` (envelope da Arquitetura 6c + guia do Kafka) e o publica (arquivo, Kafka ou nada). Também funciona sozinho: `--exemplo`, `--validar` e `--criar-topico` |
| `consumidor_teste.py` | Lê o tópico e valida cada evento como um consumidor faria (guia do Kafka, seções 4.2 e 4.3). Serve para conferir o que foi publicado |
| `comprovacaoAcertos.py` | Compara o `resultados.csv` com a placa verdadeira: acertos, erros por posição, confiança. `--comparar` põe experimentos lado a lado |
| `converterDataset-YOLO.py` | Converte o RodoSol/UFPR-ALPR para o formato YOLO (split train/val/test) e grava o `split_manifest.csv` com a placa verdadeira |
| `comprovacaoResultados.py` | Gráficos rápidos de validade de formato (usados na Sprint 2; não medem acerto) |
| `aplicarModelo-PastaImgs.py` | Primeiros testes: YOLO + EasyOCR/PaddleOCR (não está no `requirements.txt`) |
| `aplicacaoModelov4-fastalpr.py` | Histórico: Fast-ALPR completo (detector + OCR). Não usado |

## Execução com Docker

### Arquivos envolvidos

| Arquivo | Função |
|---|---|
| `Dockerfile` | Imagem `python:3.11-slim`, PyTorch para CPU, dependências do `requirements.txt`, `modelos/` e `scripts/` |
| `.dockerignore` | Mantém `.env`, `.git`, `saidas/` e o dataset fora da imagem |
| `run.sh` | Atalho que monta os volumes, passa o `.env` e executa o script pedido dentro do container |

### Uso do `run.sh`

Tudo que vem depois de `./run.sh` é executado dentro do container com `python`:

```bash
./run.sh build                                          # constrói a imagem (parkflow-i7)
./run.sh                                                # pipeline, 20 imagens por pasta (teste rápido)
./run.sh scripts/aplicacaoModelov5-fast+rodosol.py      # pipeline completo, todas as imagens
./run.sh scripts/comprovacaoAcertos.py --saida saidas   # mede os acertos
./run.sh scripts/comprovacaoAcertos.py --comparar saidas/exp_a saidas/exp_b
./run.sh scripts/publicador_eventos.py --exemplo        # evento de exemplo
./run.sh scripts/consumidor_teste.py                    # confere o que chegou no Kafka
```

O `run.sh` monta três volumes:

| Volume | Destino no container | Para quê |
|---|---|---|
| `./datasetConvertidoYOLO` | `/app/datasetConvertidoYOLO` | Imagens de entrada (e o `split_manifest.csv`) |
| `./saidas` | `/app/saidas` | Mosaicos, CSVs e `eventos.jsonl` aparecem na sua máquina |
| `i7-ocr-cache` (volume nomeado) | `/root/.cache/fast-plate-ocr` | Guarda o modelo OCR entre execuções |

### Comando completo (sem o `run.sh`)

Equivale ao `run.sh` e serve para Windows sem Git Bash ou para quem quiser ver o que acontece.

```bash
# Linux / macOS / Git Bash
docker run --rm --env-file .env \
  -v "$(pwd)/datasetConvertidoYOLO:/app/datasetConvertidoYOLO" \
  -v "$(pwd)/saidas:/app/saidas" \
  -v i7-ocr-cache:/root/.cache/fast-plate-ocr \
  parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py -n 20
```

```powershell
# Windows (PowerShell)
docker run --rm --env-file .env `
  -v "${PWD}/datasetConvertidoYOLO:/app/datasetConvertidoYOLO" `
  -v "${PWD}/saidas:/app/saidas" `
  -v i7-ocr-cache:/root/.cache/fast-plate-ocr `
  parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py -n 20
```

### Cuidados

- **`.env`:** valores **sem aspas** (o `--env-file` não as remove) e caminhos **relativos** à raiz do projeto, como `PASTA_SAIDA=saidas`. Caminhos do Windows (`C:\...`) não funcionam dentro do container.
- **Alterou um script ou o `requirements.txt`?** Rode `./run.sh build` de novo. Os scripts são copiados para dentro da imagem na construção.
- **Desenvolvendo sem reconstruir a cada mudança:** acrescente `-v "$(pwd)/scripts:/app/scripts"` ao comando completo, para o container usar sempre a versão atual dos scripts. Antes de integrar na `main`, faça um `./run.sh build` final sem esse volume.
- **Arquivos em `saidas/` com dono `root` (Linux):** o container roda como root. Se não conseguir editar ou apagar, use `sudo chown -R $USER:$USER saidas`.
- **Permissão no Linux (`permission denied` ao usar `docker`):** adicione seu usuário ao grupo com `sudo usermod -aG docker $USER` e saia e entre de novo na sessão.
- **Kafka:** o container acessa o broker pela rede da máquina. Basta `PUBLICACAO_MODO=kafka` e `KAFKA_BROKER` no `.env`.
- **Dependências do projeto:** não instale nada com `pip` na sua máquina para rodar o componente. Tudo vem na imagem.

## Instalação local (alternativa, sem Docker)

Use apenas para depurar. O caminho oficial é o container.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Requer Python 3.10 ou mais novo. O `fast-alpr` precisa do extra `[onnx]` (já está no `requirements.txt`). Sem GPU, instale antes o PyTorch para CPU (`pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`), que é bem menor.

Nesse modo, os scripts rodam com `python scripts/<arquivo>.py`, em vez de `./run.sh scripts/<arquivo>.py`.

## Configuração (`.env`)

Copie `.env.example` para `.env` e ajuste. Caminhos relativos partem da raiz do projeto. O `.env` não vai para o Git (o `.env.example` vai). Valores definidos no terminal têm prioridade sobre o `.env`. No Docker, use valores sem aspas.

| Variável | Padrão no código | Valor recomendado | O que faz |
|---|---|---|---|
| `PASTA_IMAGENS` | `datasetConvertidoYOLO` | pastas `.../images/test` | Uma pasta ou várias, separadas por vírgula |
| `PASTA_SAIDA` | `saidas` | `saidas` | Saída (com várias pastas de entrada, uma subpasta por categoria) |
| `PASTA_DATASET` | `datasetConvertidoYOLO` | idem | Raiz do dataset (usada pelo `comprovacaoAcertos.py`) |
| `CAMINHO_MODELO_YOLO` | `modelos/modelo4classesV1/best.pt` | idem | Modelo de detecção |
| `OCR_MODELO` | `global-plates-mobile-vit-v2-model` | **`cct-s-v2-global-model`** | Modelo de OCR |
| `MARGEM_RECORTE_MERCOSUL_PCT` | vazio (usa a geral) | **`10`** | Margem (% da caixa) só nas placas Mercosul |
| `MARGEM_RECORTE_PCT` | `0` | `0` | Margem para as demais placas |
| `PREPROCESSAMENTO` | `sim` | `sim` | Cinza + 2x + CLAHE + filtro antes do OCR |
| `PUBLICACAO_MODO` | `arquivo` | `arquivo` (`kafka` só para publicar de verdade) | `arquivo` grava `eventos.jsonl`; `kafka` envia ao broker; `desligado` não gera eventos |
| `SOURCE_ID` | `cam-entrada` | `cam-entrada` | Câmera/portão de origem do evento |
| `ARTIFACT_VERSION` | modelo YOLO + OCR | opcional | Versão do artefato no evento |
| `LIMIAR_INCERTEZA` | `0.9` | `0.9` | Se a menor confiança entre os caracteres ficar abaixo disso, o evento sai com `uncertain: true` |
| `KAFKA_TOPICO` | `ods.i7.plate_read` | idem | Tópico e campo `schema` dos eventos |
| `KAFKA_BROKER` | `34.66.235.91:9093` | idem (temporário) | Endereço do broker; na versão final será local |

Experimentais (sem ganho nos testes, ficam desligados): `DOBRAR_2L` (`nao`/`auto`/`fixo`/`separar`; `separar` lê cada linha da placa de moto sozinha e junta o resultado, ainda em avaliação), `DOBRAR_DIVISAO_PCT`, `DOBRAR_FOLGA_PCT`, `DOBRAR_DESCARTE_TOPO_PCT`. Pré-processamento do recorte: `PRE_ALTURA_ALVO=160` (amplia o recorte até essa altura em pixels) e `PRE_PROPORCAO=2` (completa as laterais do recorte até uma proporção largura/altura, em vez de esticar) estão recomendados no `.env.example`: nas 400 motos subiram de 71,8% para 75,5% de placas corretas e os carros ficaram iguais. `PRE_NITIDEZ` (realce de nitidez) piorou nos testes e fica em 0. Com 0 em todos, vale o comportamento original.

## Como usar

Com Docker (recomendado):

```bash
# processa as imagens (-n N processa só as N primeiras de cada pasta, para testes rápidos)
./run.sh scripts/aplicacaoModelov5-fast+rodosol.py -n 20

# mede quantas placas estavam corretas e gera o painel de gráficos
./run.sh scripts/comprovacaoAcertos.py --saida saidas

# compara experimentos (a primeira pasta é a referência)
./run.sh scripts/comprovacaoAcertos.py --comparar saidas/exp_a saidas/exp_b
```

Sem Docker, troque `./run.sh` por `python` (por exemplo, `python scripts/comprovacaoAcertos.py --saida saidas`).

Para comparar configurações, rode o pipeline uma vez para cada uma, cada qual com seu `PASTA_SAIDA`. Rode sobre as pastas `images/<split>` do dataset convertido, para os nomes dos arquivos baterem com o `split_manifest.csv`. Use `val` para decidir e `test` só para o número final.

## Publicação de eventos (Kafka)

Cada leitura **aceita pela validação de formato** vira um evento `ods.i7.plate_read`. Leituras recusadas não geram evento.

**Envelope** (guia do Kafka B2 + Arquitetura 6c): `message_type` (`event`), `message_id`, `schema` (igual ao nome do tópico), `schema_version`, `producer` (`I7`), `occurred_at`, `published_at`, `captured_at`, `source_id`, `session_id`, `sequence`, `artifact_version` e `payload`. O `occurred_at` **não consta na arquitetura 6c** (que usa `captured_at` e `published_at`): foi acrescentado porque o guia do Kafka B2 o define como campo obrigatório do envelope. Tem o mesmo valor de `captured_at` (quando o veículo foi visto).

**Payload:**

| Campo | Significado |
|---|---|
| `license_plate` | Placa lida, 7 caracteres, já validada |
| `plate_layout` | `MERCOSUL` ou `OLD_BR` |
| `class` | `car` (placa de 1 linha) ou `motorcycle` (2 linhas). Inferido da classe do YOLO |
| `confidence` | **Menor** confiança entre os 7 caracteres (0 a 1). Foi a medida que melhor separou leituras certas de erradas; use ≥ 0,9 como regra de liberação |
| `character_confidences` | Lista com a confiança (0 a 1) de cada um dos 7 caracteres da placa final, na mesma ordem de `license_plate` |
| `uncertain` | `true` se a menor confiança (`confidence`) ficou abaixo de `LIMIAR_INCERTEZA` |
| `detection_id` | Número da detecção dentro do frame (0, 1, 2…). Por enquanto é só um índice; quando houver acompanhamento da placa entre frames, passará a identificar o veículo |
| `bbox` | `[x_min, y_min, x_max, y_max]` da placa na imagem |
| `frame_index` | Posição da imagem dentro da pasta processada (começa em 0) |

```json
{
  "message_type": "event", "message_id": "…", "schema": "ods.i7.plate_read", "schema_version": "1.0",
  "producer": "I7", "occurred_at": "2026-11-04T11:34:44.886Z", "published_at": "2026-11-04T11:34:44.886Z",
  "captured_at": "2026-11-04T11:34:44.886Z", "source_id": "cam-entrada", "session_id": "…", "sequence": 0,
  "artifact_version": "modelo4classesV1+cct-s-v2-global",
  "payload": {"frame_index": 0, "class": "car", "plate_layout": "MERCOSUL",
              "detection_id": 0, "license_plate": "BRA2E19", "confidence": 0.94,
              "character_confidences": [0.99, 0.98, 0.97, 0.95, 0.94, 0.96, 0.98], "uncertain": false,
              "bbox": [1020, 640, 1190, 700]}
}
```

Um evento de exemplo completo sai de `./run.sh scripts/publicador_eventos.py --exemplo`.

**Para publicar no Kafka:**

1. No `.env`: `PUBLICACAO_MODO=kafka`, e confira `KAFKA_BROKER` e `KAFKA_TOPICO`.
2. Na primeira vez, crie o tópico (se ele ainda não existir): `./run.sh scripts/publicador_eventos.py --criar-topico`.
3. Rode o pipeline com poucas imagens (`./run.sh scripts/aplicacaoModelov5-fast+rodosol.py -n 2`). O resumo de cada pasta mostra quantos eventos o broker confirmou.
4. Confira o que chegou: `./run.sh scripts/consumidor_teste.py`. Ele para sozinho depois de 15 s sem mensagens e não grava posição de leitura, então pode ser repetido.

**Cuidados com o broker (compartilhado, na nuvem):**

- Cada execução usa **um só produtor** e termina com `flush`; não há laços de reenvio.
- O consumidor de teste faz poll de 1 s e para sozinho. Não deixe consumidores ou produtores de teste rodando em segundo plano.
- Use `-n` pequeno ao publicar. Rodadas grandes de avaliação (800 imagens) devem usar `PUBLICACAO_MODO=arquivo`.
- Quem consome deve tratar mensagens duplicadas (use `message_id`). O `sequence` reinicia em 0 a cada execução; o `session_id` muda.
- Os eventos dos testes de integração ficam no tópico (têm `source_id` `cam-entrada`). Não são leituras reais.

## Saídas (em cada pasta de saída)

Com Docker, as saídas aparecem na pasta `saidas/` da sua máquina.

- `<imagem>_mosaico.jpg`: imagem com a detecção, o recorte que o OCR viu e o texto lido.
- `resultados.csv`: imagem, placa lida, tipo, confiança da detecção, confiança média do OCR, **texto bruto do OCR**, confiança mínima e confiança por caractere.
- `eventos.jsonl`: um evento `ods.i7.plate_read` por leitura aceita pela validação (só no modo `arquivo`).
- `experimento.json`: a configuração usada na execução.
- Pelo `comprovacaoAcertos.py`: `acertos_detalhado.csv` (placa real × lida por imagem), `dashboard_acertos.png` e, com `--comparar`, `comparacao_experimentos.csv`.

## Como chegamos a esta configuração

Todos os números são de acertos exatos contra a placa verdadeira.

| Experimento | Resultado |
|---|---|
| OCR inicial (`global-plates-mobile-vit-v2`), 200 imagens de `val` | 61,0% |
| Troca para `cct-s-v2-global` | 77,0% |
| + margem de 10% em todas as placas | 81,5% (piora as antigas) |
| **+ margem de 10% só nas Mercosul** | **82,0%** |
| Medição final, 800 imagens de `test` | **84,0%** |

Testados e descartados (80 imagens de `test`): remover o pré-processamento (59 → 56 acertos com o OCR novo; sem efeito com o antigo), "dobrar" a placa de moto em uma linha (ajudou o OCR antigo, 47 → 52, mas piorou o novo, 59 → 48), folga no corte e descarte da faixa "BRASIL" (sem ganho).

## Observações e limitações

- O formato válido da placa não garante leitura correta: a regex pode aprovar uma leitura errada. Por isso a avaliação compara com a placa verdadeira.
- O erro mais comum é a primeira letra: `O` lido como `D` (15 casos em 800).
- Os scripts de treino dos modelos YOLO (e a geração dos rótulos por classe) não estão neste repositório. O `converterDataset-YOLO.py` gera apenas a classe `plate`.
- `detection_id` é, por ora, apenas o índice da detecção no frame; não acompanha o mesmo veículo entre frames (previsto para quando houver tracking/votação).
- O evento segue o contrato `ods.i7.plate_read` da arquitetura 6c (`schema_version` 1.0), com algumas diferenças de significado nos campos `confidence`, `uncertain` e `detection_id`. Elas estão listadas em `MUDANCAS_VER5.md`. Mensagens publicadas antes de 04/11/2026 não têm `detection_id`, `character_confidences` e `uncertain`.
- O campo `class` do evento é inferido: 1 linha = `car`, 2 linhas = `motorcycle` (a confirmar com o TRA-1).
- O nome do tópico (`ods.i7.plate_read`) segue o padrão `ods.<componente>.<evento>` dos tópicos já existentes (`ods.i1.detections`, `ods.i6.code_read`), e não os domínios sugeridos no guia (que lista `ods.veiculo.placa_lida` para o I7). A confirmar com o TRA-1.
- O broker é temporário (Google Cloud, compartilhado); a versão final usará um broker local, e só o `KAFKA_BROKER` precisa mudar.