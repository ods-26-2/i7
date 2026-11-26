# ParkFlow · I7: leitura de placas (detecção + OCR)

> ## ⚠️ LEIA PRIMEIRO: [`MUDANCAS_VER5.md`](MUDANCAS_VER5.md)
> Existe um segundo documento, curto, com o que a nossa squad **mudou em relação à arquitetura 6c** (escopo, cancela simulada, diferenças no payload do I7) e **como usar os arquivos**. Abra-o antes de rodar qualquer coisa.


Componente de **reconhecimento automático de placas** para controle de acesso veicular. A câmera captura o veículo, o sistema localiza a placa, lê os caracteres, valida o formato (Mercosul ou antiga) e entrega a leitura, com a confiança, ao restante do sistema (A8) pelo evento `ods.i7.plate_read`, publicado no Kafka.

## Fluxo

```
imagem (pasta), quadro de vídeo (--video) ou quadro da câmera do P4 (--p4)
       → detector YOLO (4 classes) → recorte (+ margem nas Mercosul) → OCR (cct-s-v2-global)
       → correção posicional + regex → resultado
                                         ├─ mosaico + resultados.csv
                                         └─ evento ods.i7.plate_read (no vídeo: só confiança >= 0,9 e sem repetir a placa)
                                              ├─ modo arquivo: eventos.jsonl (sem rede)
                                              └─ modo kafka:   tópico ods.i7.plate_read no broker
```

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

**OCR:** `cct-s-v2-global-model`, da biblioteca [fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr) (usada pelo Fast-ALPR). O modelo é baixado na primeira execução. Se a sua rede bloquear o download (erro `CERTIFICATE_VERIFY_FAILED`), baixe os dois arquivos à mão:

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

## Arquivos (`scripts/`)

| Arquivo | Função |
|---|---|
| `aplicacaoModelov5-fast+rodosol.py` | **Pipeline atual:** YOLO de 4 classes + OCR + validação, com mosaicos, CSV e publicação de eventos. Lê pastas de imagens ou um vídeo (`--video`) |
| `webcam_mjpeg.py` | Transmite a webcam (ou um vídeo em laço) como MJPEG por HTTP, para o I7 ler ao vivo com `--video <endereço>`. Roda fora do Docker |
| `fonte_ao_vivo.py` | Lê uma transmissão ao vivo por endereço, guardando só o quadro mais recente. Usado pelo `--video` com endereço |
| `fonte_p4.py` | Lê a stream do P4 (memória compartilhada local, protocolo `P4R1`) e converte os quadros para imagem. Usado pelo `--p4`; sozinho, `python scripts/fonte_p4.py video` testa a conexão |
| `cortar_video.py` | Corta um trecho de um vídeo (sem `ffmpeg`), por exemplo para usar como câmera simulada do P4 |
| `baixar_video.py` | Baixa um vídeo (ex.: do YouTube, com `yt-dlp`) para a pasta `videos/`, para depois rodar o pipeline com `--video` |
| `publicador_eventos.py` | Monta e valida o evento `ods.i7.plate_read` (envelope da Arquitetura 6c + guia do Kafka) e o publica (arquivo, Kafka ou nada). Também funciona sozinho: `--exemplo`, `--validar` e `--criar-topico` |
| `consumidor_teste.py` | Lê o tópico e valida cada evento como um consumidor faria (guia do Kafka, seções 4.2 e 4.3). Serve para conferir o que foi publicado. Modos: sem opção (relê tudo), `--novas` (só o que ainda não leu) e `--ao-vivo` (só o que chegar com ele ligado) |
| `comprovacaoAcertos.py` | Compara o `resultados.csv` com a placa verdadeira: acertos, erros por posição, confiança. `--comparar` põe experimentos lado a lado |
| `converterDataset-YOLO.py` | Converte o RodoSol/UFPR-ALPR para o formato YOLO (split train/val/test) e grava o `split_manifest.csv` com a placa verdadeira |
| `comprovacaoResultados.py` | Gráficos rápidos de validade de formato (usados na Sprint 2; não medem acerto) |
| `aplicarModelo-PastaImgs.py` | Primeiros testes: YOLO + EasyOCR/PaddleOCR (não está no `requirements.txt`) |
| `aplicacaoModelov4-fastalpr.py` | Histórico: Fast-ALPR completo (detector + OCR). Não usado |

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Requer Python 3.10 ou mais novo. O `fast-alpr` precisa do extra `[onnx]` (já está no `requirements.txt`). Sem GPU, instale antes o PyTorch para CPU (`pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`), que é bem menor.

## Configuração (`.env`)

Copie `.env.example` para `.env` e ajuste. Caminhos relativos partem da raiz do projeto. O `.env` não vai para o Git (o `.env.example` vai). Valores definidos no terminal têm prioridade sobre o `.env`.

| Variável | Padrão no código | Valor recomendado | O que faz |
|---|---|---|---|
| `PASTA_IMAGENS` | `datasetConvertidoYOLO` | pastas `.../images/test` | Uma pasta ou várias, separadas por vírgula |
| `PASTA_SAIDA` | `saidas` | `saidas` | Saída (com várias pastas de entrada, uma subpasta por categoria) |
| `PASTA_DATASET` | `datasetConvertidoYOLO` | idem | Raiz do dataset (usada pelo `comprovacaoAcertos.py`) |
| `CAMINHO_MODELO_YOLO` | `modelos/modelo4classesV1/best.pt` | idem | Modelo de detecção |
| `DETECTOR` | `ultralytics` | **`p2`** (ou `ultralytics`, ver abaixo) | `ultralytics` usa o `best.pt`; `p2` usa o pacote do B5 pela biblioteca P2 |
| `P2_ENGINE_REF` | `app-plates-1.0.0` | idem | Engine do P2 (só com `DETECTOR=p2`) |
| `NMS_ENTRE_CLASSES` | `nao` | **`sim`** | Com `sim`, caixas sobrepostas de classes diferentes se eliminam (fica a de maior confiança): evita a mesma placa detectada duas vezes, como antiga e como Mercosul. Testado em vídeo; falta medir nas 800 imagens |
| `OCR_MODELO` | `global-plates-mobile-vit-v2-model` | **`cct-s-v2-global-model`** | Modelo de OCR |
| `MARGEM_RECORTE_MERCOSUL_PCT` | vazio (usa a geral) | **`10`** | Margem (% da caixa) só nas placas Mercosul |
| `MARGEM_RECORTE_PCT` | `0` | `0` | Margem para as demais placas |
| `PREPROCESSAMENTO` | `sim` | `sim` | Cinza + 2x + CLAHE + filtro antes do OCR |
| `PUBLICACAO_MODO` | `arquivo` | `arquivo` (`kafka` só para publicar de verdade) | `arquivo` grava `eventos.jsonl`; `kafka` envia ao broker; `desligado` não gera eventos |
| `SOURCE_ID` | `cam-entrada` | `cam-entrada` | Câmera/portão de origem do evento |
| `ARTIFACT_VERSION` | detector + OCR | opcional | Versão do artefato no evento (com `DETECTOR=p2`: `app-plates-1.0.0-fp32+cct-s-v2-global`) |
| `LIMIAR_INCERTEZA` | `0.9` | `0.9` | Se a menor confiança entre os caracteres ficar abaixo disso, o evento sai com `uncertain: true` |
| `VIDEO_QUADROS_POR_SEGUNDO` | `2` | `2` | Só com `--video`: quadros processados por segundo do vídeo (os outros são pulados) |
| `VIDEO_JANELA_REPETICAO_S` | `10` | `10` | Só com `--video`: a mesma placa só volta a ser publicada depois de ficar esse tempo (do vídeo) sem ser lida |
| `VIDEO_CONFIANCA_MINIMA` | `LIMIAR_INCERTEZA` (0.9) | `0.9` | Só com `--video` ou `--p4`: só vira evento a leitura com confiança mínima >= este valor. `0` publica todas |
| `P4_ESPERA_S` | `30` | `30` | Só com `--p4`: encerra a leitura depois deste tempo (s) sem quadros |
| `SALVAR_MOSAICOS` | `publicados` | `publicados` | Só com `--video` ou `--p4`: `publicados` salva o mosaico só dos quadros em que alguma leitura virou evento; `todos`, todo quadro com placa; `nenhum`, nenhum (o CSV continua) |
| `P4_BAYER` | `RGGB` | `RGGB` | Só com `--p4` e câmeras físicas: padrão do mosaico Bayer do `RG10`. Se as cores saírem trocadas, `BGGR` |
| `KAFKA_TOPICO` | `ods.i7.plate_read` | idem | Tópico e campo `schema` dos eventos |
| `KAFKA_BROKER` | `34.66.235.91:9093` | o **Kafka da nuvem** (endereço a confirmar com o TRA) | Na homologação, o I7 roda na Jetson e publica no Kafka da nuvem, onde o A8 consome. O Kafka da Jetson só serve para testes (ver "Acesso ao broker da Jetson") |

Experimentais (sem ganho nos testes, ficam desligados): `DOBRAR_2L` (`nao`/`auto`/`fixo`/`separar`; `separar` lê cada linha da placa de moto sozinha e junta o resultado, ainda em avaliação), `DOBRAR_DIVISAO_PCT`, `DOBRAR_FOLGA_PCT`, `DOBRAR_DESCARTE_TOPO_PCT`. Pré-processamento do recorte: `PRE_ALTURA_ALVO=160` (amplia o recorte até essa altura em pixels) e `PRE_PROPORCAO=2` (completa as laterais do recorte até uma proporção largura/altura, em vez de esticar) estão recomendados no `.env.example`: nas 400 motos subiram de 71,8% para 75,5% de placas corretas e os carros ficaram iguais. `PRE_NITIDEZ` (realce de nitidez) piorou nos testes e fica em 0. Com 0 em todos, vale o comportamento original.

**Detector via P2 (`DETECTOR=p2`).** O pacote do B5 (`app-plates` 1.0.0: `app-plates.onnx`, `metadata.json`, `descriptor.json`) fica em `modelos/app-plates-1.0.0/`. O `Dockerfile` instala a biblioteca P2 (SIS-1, sem alterá-la) e roda `p2-build --cpu-only` nesse pacote, que confere o hash do ONNX e gera o engine. O `scripts/detector_p2.py` chama `p2.load`, `p2.infer`, `p2.info` e `p2.release`, faz o pré-processamento (letterbox, BGR→RGB, ÷255) e o pós-processamento (limiar 0,25, NMS 0,7, volta às coordenadas da imagem) e devolve as caixas no mesmo formato da ultralytics, então o resto do pipeline não muda. Com `DETECTOR=p2`, o `artifact_version` dos eventos é a versão que o P2 informa (`app-plates-1.0.0-fp32` na CPU; `-fp16` na Jetson). Em 200 imagens (50 por pasta), o P2 e a ultralytics acertaram o mesmo total (167, 83,5%) e o P2 levou 1 min 55 s contra 3 min 59 s. O OCR continua fora do P2. Fora do Docker: `pip install --no-deps https://github.com/ods-26-2/p2/releases/download/v0.1.0/p2-0.1.0-py3-none-any.whl onnx`, depois `p2-build modelos/app-plates-1.0.0 --cpu-only --engine-dir <pasta>` e `P2_ENGINE_DIR=<pasta>`.

## Como usar

```bash
# processa as imagens (-n N processa só as N primeiras de cada pasta, para testes rápidos)
python scripts/aplicacaoModelov5-fast+rodosol.py -n 20

# mede quantas placas estavam corretas e gera o painel de gráficos
python scripts/comprovacaoAcertos.py --saida saidas

# compara experimentos (a primeira pasta é a referência)
python scripts/comprovacaoAcertos.py --comparar saidas/exp_a saidas/exp_b
```

Para comparar configurações, rode o pipeline uma vez para cada uma, cada qual com seu `PASTA_SAIDA`. Rode sobre as pastas `images/<split>` do dataset convertido, para os nomes dos arquivos baterem com o `split_manifest.csv`. Use `val` para decidir e `test` só para o número final.

### Vídeo

```bash
# baixa um vídeo para videos/ (só a imagem, até 1080p; --altura-max 720 para arquivos menores)
python scripts/baixar_video.py "https://www.youtube.com/watch?v=..." --nome portao

# processa o vídeo inteiro, ou só um trecho (segundos ou minutos:segundos)
python scripts/aplicacaoModelov5-fast+rodosol.py --video videos/portao.mp4
python scripts/aplicacaoModelov5-fast+rodosol.py --video videos/portao.mp4 --inicio 1:30 --fim 2:10
```

- **Amostragem:** processa `VIDEO_QUADROS_POR_SEGUNDO` quadros por segundo do vídeo (padrão 2, o ritmo do pipeline na CPU). Num vídeo a 30 q/s, 1 a cada 15 quadros. O `-n N` limita aos N primeiros quadros amostrados.
- **Uma leitura por veículo:** a mesma placa só volta a ser publicada depois de ficar `VIDEO_JANELA_REPETICAO_S` segundos (padrão 10, tempo do vídeo) sem ser lida; cada nova leitura reinicia a contagem, então um carro parado no portão gera um evento só. A janela é por texto de placa: placas diferentes são publicadas normalmente, mesmo no mesmo quadro.
- **Só leituras confiáveis:** no vídeo, só vira evento a leitura com confiança mínima >= `VIDEO_CONFIANCA_MINIMA` (padrão 0,9). Um carro que se aproxima começa borrado; as leituras ruins do começo ficam só no CSV e a leitura nítida dos quadros seguintes é a publicada. Nas imagens nada muda: toda leitura aceita vira evento (com `uncertain` abaixo de 0,9).
- **Saída** em `PASTA_SAIDA/<nome do vídeo>` (com trecho: `<nome>_90-130s`). O `resultados.csv` tem uma linha por leitura, com `quadro`, `tempo_s` e `publicado` (`sim`, `repetida`, `baixa_confianca` ou vazio, quando a validação recusou). Só os quadros com placa geram mosaico.
- O `frame_index` do evento é o número real do quadro no vídeo, e o `captured_at` é a hora em que o quadro foi lido (o arquivo não guarda a hora da gravação).
- Vídeos ficam na pasta `videos/`, que não vai para o Git. Baixar do YouTube pode ferir os termos do site: use para testes e com vídeos que vocês possam usar. Se o `yt-dlp` falhar, coloque o arquivo em `videos/` por outro meio.

Testes com vídeos de demonstração de câmeras LPR (placas brasileiras): as placas nítidas foram lidas corretamente e publicadas uma vez cada; as leituras erradas de placas ainda distantes ficaram abaixo de 0,9 e não viraram evento.

### Ao vivo pela webcam (demonstração)

O `--video` também aceita um endereço (`http://...` ou `rtsp://...`) e lê ao vivo, sempre o quadro mais recente (sem acumular atraso), com as mesmas regras e saídas do P4 (pasta `saidas/ao_vivo`). Para usar a webcam do notebook (o Docker do Windows não enxerga a webcam, então ela é transmitida de fora):

```bash
# terminal 1, FORA do Docker (precisa de: pip install opencv-python)
python scripts/webcam_mjpeg.py --previa
# terminal 2: use o endereço que o terminal 1 mostrar
./run.sh scripts/aplicacaoModelov5-fast+rodosol.py --video "http://host.docker.internal:8080/video?t=..."
```

O endereço leva um código aleatório (`?t=...`) a cada execução, para a webcam não ficar aberta para a rede. A webcam tem pouca resolução: a placa precisa ocupar boa parte da imagem (placa impressa ou na tela do celular, a meio metro). Isso não passa pelo P4; para testar a integração, use o `--p4`. `P4_ESPERA_S` também vale aqui (encerra após esse tempo sem quadros).

### Stream do P4 (câmeras da Jetson)

O P4 (SIS-2) publica cada quadro numa memória compartilhada **local** (um socket por câmera em `/tmp`, protocolo `P4R1`). Com `--p4 imx519`, `--p4 imx477` ou `--p4 video` (MP4 reproduzido como câmera), o I7 lê esses quadros e aplica as mesmas regras do vídeo (janela de repetição e confiança mínima, com o tempo real de leitura). O I7 pega sempre o quadro mais recente; os que chegam enquanto ele processa são descartados, sem acumular atraso. O `frame_index` do evento é o `frame_id` do P4 e o `session_id` é o da sessão do P4. Para com `-n`, Ctrl+C ou `P4_ESPERA_S` segundos sem quadros; se o P4 ainda não estiver ligado, espera.

Só funciona na mesma máquina do P4: na Jetson. O passo a passo para lá está em [`RODAR_NA_JETSON.md`](RODAR_NA_JETSON.md).

**Teste local, sem a Jetson** (Docker; a pasta `teste_p4/` roda a câmera simulada do P4 a partir do repositório deles, sem alterá-lo):

```bash
docker build -t p4-teste teste_p4
python scripts/cortar_video.py videos/parkingcam.mp4 --inicio 2:27 --fim 3:03   # ou ./run.sh scripts/cortar_video.py ...
docker volume create p4-tmp

# terminal 1: o I7 (espera o P4)
docker run --rm -it --env-file .env --ipc=host -v p4-tmp:/tmp -v "$(pwd)/saidas:/app/saidas" \
  -v i7-ocr-cache:/root/.cache/fast-plate-ocr parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py --p4 video

# terminal 2: o P4 reproduzindo o vídeo como câmera
docker run --rm -it --ipc=host -v p4-tmp:/tmp -v "$(pwd)/videos:/videos" \
  -e P4_VIDEO_PATH=/videos/parkingcam_147-183s.mp4 p4-teste
```

Testado assim no Windows (Docker Desktop): o I7 conectou sozinho quando o P4 ligou e publicou as mesmas 3 placas do teste com `--video`. Se o P4 disser que o socket já existe (sobra de uma execução interrompida): `docker volume rm p4-tmp` e `docker volume create p4-tmp`.

**Cores das câmeras físicas:** o `RG10` é RAW Bayer (RGGB). No OpenCV, a conversão certa para RGGB é `COLOR_BayerBG2BGR` (os nomes são "deslocados"); o leitor de exemplo do P4 usa `COLOR_BayerRG2BGR`, que troca vermelho e azul. Confirmado com imagem sintética, ainda não com a câmera real: confira um mosaico na Jetson e, se precisar, use `P4_BAYER`.

## Publicação de eventos (Kafka)

Cada leitura **aceita pela validação de formato** vira um evento `ods.i7.plate_read`. Leituras recusadas não geram evento.

**Validação:** antes de gravar ou enviar, cada evento passa pela validação própria (`validar_evento`, mais rígida) e pela biblioteca oficial **`ods-validator`** (TRA-2; JSON Schemas da Especificação 1.0, repositório `ods-26-2/b1`, versão fixada no `requirements.txt`). Evento que falha em qualquer uma é descartado, com o motivo no terminal. Para conferir um arquivo: `python scripts/publicador_eventos.py --validar saidas/<pasta>/eventos.jsonl`. Os eventos dos testes com imagens, vídeo e P4 passaram no schema oficial.

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

Um evento de exemplo completo sai de `python scripts/publicador_eventos.py --exemplo`.

**Para publicar no Kafka:**

1. No `.env`: `PUBLICACAO_MODO=kafka`, e confira `KAFKA_BROKER` e `KAFKA_TOPICO`.
2. Na primeira vez, crie o tópico (se ele ainda não existir): `python scripts/publicador_eventos.py --criar-topico`.
3. Rode o pipeline com poucas imagens (`-n 2`). O resumo de cada pasta mostra quantos eventos o broker confirmou.
4. Confira o que chegou com `python scripts/consumidor_teste.py`. Ele para sozinho depois de 15 s sem mensagens (`--espera`) ou ao ler 50 (`--max`). Há três modos:
   - sem opção: relê o tópico desde o início e não grava posição, então pode ser repetido;
   - `--novas`: lê só o que este consumidor ainda não leu e grava a posição ao terminar (`group.id` próprio, `I7-verificacao-novas`; na primeira vez lê tudo);
   - `--ao-vivo`: lê só o que for publicado com ele ligado. Inicie-o, espere uns 5 s e publique; ele não altera a posição do `--novas`.

   Nenhum dos modos usa o `group.id` do A8 (`A8-acesso`), então conferir por aqui não atrapalha o app.

### Acesso ao broker da Jetson

> **Na homologação, o I7 publica no Kafka da nuvem** (arquitetura definida em 23/11: camadas de plataforma e inferência na Jetson, barramento e camadas de cima na nuvem). Esta seção só vale para testes no Kafka **local** da Jetson.

O broker (`172.18.4.56:9092`, sem TLS) fica na rede do Dcomp. De dentro dela use esse endereço; de fora, o público (`200.17.66.56:9092`), que não funciona de dentro. Teste a porta antes: `nc -zv -w 5 <ip> 9092` (Linux) ou `Test-NetConnection <ip> -Port 9092` (PowerShell).

**Problema do `localhost:9092`.** O broker ainda anuncia `localhost:9092` (`advertised.listeners`). O cliente conecta, recebe esse endereço e falha com `Connect to ipv4#127.0.0.1:9092 failed`. A correção definitiva é no broker (anunciar o endereço real). Enquanto isso, use uma **ponte** (`socat`) e rode o container na rede dela. São dois terminais:

```bash
# Terminal 1 (fica aberto; Ctrl+C no fim). Troque <ip> pelo endereço que vale para você.
docker rm -f ponte
docker run --rm --name ponte alpine/socat TCP-LISTEN:9092,fork,reuseaddr TCP:<ip>:9092
```

```bash
# Terminal 2, na pasta do projeto (Linux / Git Bash). No PowerShell, troque \ por ` e $(pwd) por ${PWD}.
docker run --rm --env-file .env --network container:ponte \
  -v "$(pwd)/datasetConvertidoYOLO:/app/datasetConvertidoYOLO" \
  -v "$(pwd)/saidas:/app/saidas" \
  -v "$(pwd)/videos:/app/videos" \
  -v i7-ocr-cache:/root/.cache/fast-plate-ocr \
  parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py -n 2

# O consumidor usa a mesma ponte (não precisa dos volumes):
docker run --rm --env-file .env --network container:ponte parkflow-i7 scripts/consumidor_teste.py --novas
```

O `run.sh` e o `run.ps1` não usam a rede da ponte; com a ponte, use o `docker run` acima. Se o `.env` tiver o endereço de um lado e você estiver do outro, acrescente `-e KAFKA_BROKER=localhost:9092` ao `docker run`. Sem a ponte rodando, o erro volta a ser `localhost:9092 ... Connection refused`.

Se o `docker network` do seu computador (por exemplo, a rede `a8_default` do Compose do A8) usar a faixa `172.18.0.0/16`, os containers não alcançam `172.18.4.56`. Remova essa rede (`docker network rm`) ou mude a sub-rede dela.

**Cuidados com o broker (compartilhado, na Jetson):**

- Cada execução usa **um só produtor** e termina com `flush`; não há laços de reenvio.
- O consumidor de teste faz poll de 1 s e para sozinho. Não deixe consumidores ou produtores de teste rodando em segundo plano.
- Use `-n` pequeno ao publicar. Rodadas grandes de avaliação (800 imagens) devem usar `PUBLICACAO_MODO=arquivo`.
- Quem consome deve tratar mensagens duplicadas (use `message_id`). O `sequence` reinicia em 0 a cada execução; o `session_id` muda.
- Os eventos dos testes de integração ficam no tópico (têm `source_id` `cam-entrada`). Não são leituras reais.

## Saídas (em cada pasta de saída)

- `<imagem>_mosaico.jpg`: imagem com a detecção, o recorte que o OCR viu e o texto lido.
- `resultados.csv`: imagem, placa lida, tipo, confiança da detecção, confiança média do OCR, **texto bruto do OCR**, confiança mínima e confiança por caractere. No vídeo e no P4, `quadro` e `tempo_s` no lugar de `imagem`, mais `publicado`, `capturado_em`, `message_id` (o mesmo do evento no Kafka) e `mosaico` (o arquivo daquela leitura): a partir de uma decisão do A8, dá para achar o mosaico da leitura.
- No vídeo e no P4, só os quadros com evento publicado geram mosaico (`SALVAR_MOSAICOS=publicados`). No P4 o `resultados.csv` acumula entre execuções e o mosaico leva data/hora UTC da captura e a placa no nome (ex.: `20261125T221530Z_EQX4670_q000123.jpg`), para nada ser sobrescrito.
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
| Recusar leituras com mais de 7 caracteres (antes eram cortadas), simulado nas 800 | acertos iguais; 4 placas erradas deixam de ser aceitas (106 → 102) |

Testados e descartados (80 imagens de `test`): remover o pré-processamento (59 → 56 acertos com o OCR novo; sem efeito com o antigo), "dobrar" a placa de moto em uma linha (ajudou o OCR antigo, 47 → 52, mas piorou o novo, 59 → 48), folga no corte e descarte da faixa "BRASIL" (sem ganho).

## Observações e limitações

- O formato válido da placa não garante leitura correta: a regex pode aprovar uma leitura errada. Por isso a avaliação compara com a placa verdadeira.
- Leituras com mais de 7 caracteres são recusadas (antes eram cortadas para 7 e podiam virar uma placa brasileira válida, mas errada; por exemplo, uma placa estrangeira `34A36837` virava `BAA6837`).
- **Placas coloridas:** o detector foi treinado com o RodoSol, quase só placas cinza (antigas) e Mercosul de carro particular. Placas vermelhas (comerciais, aluguel), pretas, azuis ou Mercosul com letras coloridas podem não ser detectadas; os 84% não dizem nada sobre elas. Melhorar isso exigiria treinar o detector com esses exemplos.
- Fora do RodoSol (câmera fixa e boa luz), a qualidade depende da câmera: placas pequenas, distantes ou borradas no vídeo geram leituras erradas, que a confiança mínima de 0,9 costuma barrar.
- O erro mais comum é a primeira letra: `O` lido como `D` (15 casos em 800).
- Os scripts de treino dos modelos YOLO (e a geração dos rótulos por classe) não estão neste repositório. O `converterDataset-YOLO.py` gera apenas a classe `plate`.
- `detection_id` é, por ora, apenas o índice da detecção no frame; não acompanha o mesmo veículo entre frames (previsto para quando houver tracking/votação).
- O evento segue o contrato `ods.i7.plate_read` da arquitetura 6c (`schema_version` 1.0), com algumas diferenças de significado nos campos `confidence`, `uncertain` e `detection_id`. Elas estão listadas em `MUDANCAS_VER5.md`. Mensagens publicadas antes de 04/11/2026 não têm `detection_id`, `character_confidences` e `uncertain`.
- O campo `class` do evento é inferido: 1 linha = `car`, 2 linhas = `motorcycle` (a confirmar com o TRA-1).
- O nome do tópico (`ods.i7.plate_read`) segue o padrão `ods.<componente>.<evento>` dos tópicos já existentes (`ods.i1.detections`, `ods.i6.code_read`), e não os domínios sugeridos no guia (que lista `ods.veiculo.placa_lida` para o I7). A confirmar com o TRA-1.
- O broker é temporário (Jetson do Dcomp, compartilhado; antes, Google Cloud); a versão final usará um broker local, e só o `KAFKA_BROKER` precisa mudar.