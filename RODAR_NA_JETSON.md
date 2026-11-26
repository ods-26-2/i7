# Rodar o I7 na Jetson, lendo as câmeras do P4

Guia para quem tem acesso à Jetson (SIS-1). O squad VER-5 não tem acesso a ela: tudo abaixo foi testado
num PC (Docker Desktop, x86) com a câmera simulada do P4, **não na Jetson**. Os pontos que só a Jetson
confirma estão marcados com ⚠️.

## Como funciona

O P4 publica cada quadro numa memória compartilhada local (um socket por câmera, em `/tmp`). O I7 roda
num container na mesma Jetson, lê esses quadros, detecta e lê as placas e publica `ods.i7.plate_read` no
**Kafka da nuvem**, onde o A8 (camada de aplicação, na nuvem) consome. Os pixels não passam pelo Kafka.
O Kafka local da Jetson não é usado pelo I7.

| Fonte do P4 | Opção do I7 | Socket | Formato |
|---|---|---|---|
| Câmera IMX519 | `--p4 imx519` | `/tmp/p4_imx519_shm` | RAW Bayer `RG10`, 1920×1080 |
| Câmera IMX477 | `--p4 imx477` | `/tmp/p4_imx477_shm` | RAW Bayer `RG10`, 1920×1080 |
| MP4 como câmera | `--p4 video` | `/tmp/p4_file_shm` | `BGR8` |

## 1. Preparar (uma vez)

```bash
git clone https://github.com/ods-26-2/i7.git
cd i7
cp .env.example .env
```

No `.env`, ajuste:

```dotenv
# publica direto no Kafka (o .env.example vem com "arquivo", que só grava em disco)
PUBLICACAO_MODO=kafka
# Kafka da NUVEM (endereço a confirmar com o TRA; este era o da nuvem até a Sprint 3)
KAFKA_BROKER=34.66.235.91:9093
# câmera/portão que esta leitura representa (o A8 usa cam-entrada ou cam-saida)
SOURCE_ID=cam-entrada
```

## 2. Construir a imagem

```bash
docker build -t parkflow-i7 .
```

⚠️ A imagem nunca foi construída em ARM64. Se algum passo falhar:

- **PyTorch** (`pip install torch ... /whl/cpu`): só é usado com `DETECTOR=ultralytics`. Com `DETECTOR=p2`
  (padrão do `.env.example`), dá para apagar essa linha do `Dockerfile`; o `ultralytics` do
  `requirements.txt` ainda puxa o PyTorch do PyPI, que tem versão para ARM64.
- **`p2-build --cpu-only`**: compila o engine do P2 para CPU. Na Jetson, o P2 também pode gerar o engine
  TensorRT (FP16), bem mais rápido; isso depende da imagem base do P3/SIS-1 e não foi feito aqui.

O modelo de OCR é baixado do GitHub na primeira execução (fica no volume `i7-ocr-cache`). Sem internet na
Jetson, copie os dois arquivos à mão (instruções no `README.md`, seção "Modelos").

## 3. Ligar o P4

Conforme o repositório do P4 (`ods-26-2/p4`), por exemplo:

```bash
./scripts/p4_demo.sh imx519          # câmera física
./scripts/p4_demo.sh video arq.mp4   # MP4 como câmera
```

O I7 só lê; não abre a câmera. Vários consumidores podem ler o mesmo socket ao mesmo tempo.

## 4. Rodar o I7 (teste curto, já publicando no Kafka)

Antes, confira se a Jetson alcança o Kafka da nuvem (troque pelo endereço do `.env`):

```bash
nc -zv -w 5 34.66.235.91 9093
```

Para ver o que chega ao Kafka, ligue antes, em outro terminal, o consumidor de teste (ele usa um
`group.id` próprio e não atrapalha o A8):

```bash
docker run --rm -it --env-file .env parkflow-i7 scripts/consumidor_teste.py --ao-vivo --espera 120
```

Espere aparecer `Modo: só as publicadas com o consumidor ligado` e, em outro terminal, na pasta do I7:

```bash
docker run --rm -it --env-file .env --ipc=host -v /tmp:/tmp \
  -v "$(pwd)/saidas:/app/saidas" -v i7-ocr-cache:/root/.cache/fast-plate-ocr \
  parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py --p4 imx519 -n 20
```

- `-v /tmp:/tmp` e `--ipc=host`: o container enxerga o socket e a memória compartilhada do P4.
- `-n 20`: para depois de 20 quadros processados. Sem `-n`, roda até Ctrl+C ou até ficar `P4_ESPERA_S`
  segundos sem quadros (padrão 30).

Deve aparecer `[P4] sessão ... | fonte imx519 | 1920x1080 RG10` e, para cada quadro com placa, a leitura.
As leituras sem `(repetida)` ou `(baixa confianca)` ao lado são as publicadas: no fim aparece
`Publicação: N evento(s) entregue(s) ao tópico ods.i7.plate_read`, e o consumidor de teste mostra as
mesmas N placas. Cada evento é validado antes do envio (validação própria + `ods-validator`, os contratos
da Especificação 1.0); um evento fora do contrato é descartado e o motivo aparece no terminal.
Os resultados ficam em `saidas/p4_imx519/` (um mosaico por evento publicado e o `resultados.csv`).

## 5. Conferir as cores ⚠️

Abra um mosaico de `saidas/p4_imx519/`. Se as cores estiverem trocadas (céu laranja, faixa da placa
Mercosul vermelha em vez de azul), mude no `.env`:

```dotenv
P4_BAYER=BGGR
```

e rode o passo 4 de novo. O padrão `RGGB` é o que o formato `RG10` indica, mas só a câmera real confirma.
(O leitor de exemplo do P4, `scripts/p4_ler.sh`, usa uma conversão que troca vermelho e azul para RGGB;
se os PNGs dele parecerem certos, a câmera provavelmente é `BGGR`.)

## 6. Conferir no A8

Com o A8 ligado ao mesmo Kafka, o log dele mostra a decisão de cada placa publicada (`liberado`,
`negado` ou `baixa_confianca`). O broker é compartilhado por todas as squads: use `-n` pequeno nos testes
e não deixe consumidores de teste rodando em segundo plano.

## 7. Rodar continuamente

Sem `-n`, com uma espera longa para o I7 não encerrar se a câmera parar por um tempo:

```bash
docker run -d --name i7 --restart unless-stopped --env-file .env -e P4_ESPERA_S=86400 \
  --ipc=host -v /tmp:/tmp \
  -v "$(pwd)/saidas:/app/saidas" -v i7-ocr-cache:/root/.cache/fast-plate-ocr \
  parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py --p4 imx519
docker logs -f i7      # acompanhar
docker stop i7         # parar
```

Combine antes com o professor: o I7 fica publicando no broker compartilhado enquanto houver placas.

**Conferência das leituras:** em `saidas/p4_<fonte>/` ficam um mosaico por evento publicado (nome com
data/hora UTC da captura e a placa, ex.: `20261125T221530Z_EQX4670_q000123.jpg`) e o `resultados.csv`, que
acumula entre execuções e liga cada leitura ao `message_id` do evento e ao mosaico. Para ver uma decisão do A8,
procure a placa ou o `message_id` no CSV e abra o mosaico indicado. Com `SALVAR_MOSAICOS=publicados`
(padrão), só as leituras publicadas geram mosaico; `todos` salva todo quadro com placa, e enche o disco
mais rápido.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Fica em `[P4] aguardando o P4 em /tmp/...` | O P4 não está rodando, a fonte está errada (`imx519`/`imx477`/`video`) ou faltou `-v /tmp:/tmp` |
| `existe, mas não é um socket do P4` | O caminho passado no `--p4` não é o socket; use `imx519`, `imx477` ou `video` |
| Conecta, mas nenhum quadro chega | Faltou `--ipc=host` |
| Erro sobre `gi` ou GStreamer | O build não instalou os pacotes do GStreamer; confira a saída do `docker build` |
| Cores trocadas nos mosaicos | Passo 5 (`P4_BAYER`) |
| `falha ao entregar ao Kafka` / `Message timed out` | A Jetson não alcança o Kafka da nuvem: confira o endereço no `.env` e o teste com `nc` do passo 4 |
| `evento descartado: contrato oficial: ...` | O evento não passou no `ods-validator`; avise o squad VER-5 com a mensagem |
| Muito lento | Na CPU, ~2 a 3 quadros por segundo (os intermediários são descartados, sem atraso acumulado). Com TensorRT no P2 ficaria bem mais rápido |