# Como usar o Docker neste projeto (I7)

O componente roda em um container Docker. Não é preciso instalar Python nem bibliotecas: só **Git** e **Docker**.

## O que precisa ter

| Sistema | Instalar |
|---|---|
| Linux | Docker Engine + Git |
| Windows | Docker Desktop (aberto e rodando) + Git for Windows (traz o Git Bash) |

Os computadores do lab já vêm com o Docker configurado.

## Linux

```bash
# 1. Liberar o Docker sem sudo (uma vez; depois, sair e entrar na sessão)
sudo usermod -aG docker $USER

# 2. Baixar e configurar
git clone https://github.com/ods-26-2/i7.git
cd i7
cp .env.example .env

# 3. Construir a imagem (só na primeira vez ou quando o código mudar)
./run.sh build

# 4. Testar
./run.sh scripts/publicador_eventos.py --exemplo
```

Se aparecer `Permission denied` no `./run.sh`: `chmod +x run.sh`.

## Windows

Abra o **Docker Desktop** e espere aparecer "Engine running". Depois, use uma das opções.

**Opção A: Git Bash** (mesmos comandos do Linux)

```bash
git clone https://github.com/ods-26-2/i7.git
cd i7
cp .env.example .env
./run.sh build
./run.sh scripts/publicador_eventos.py --exemplo
```

**Opção B: PowerShell**

```powershell
git clone https://github.com/ods-26-2/i7.git
cd i7
Copy-Item .env.example .env
.\run.ps1 build
.\run.ps1 scripts/publicador_eventos.py --exemplo
```

Se o PowerShell bloquear o script: `Set-ExecutionPolicy -Scope Process Bypass` (vale só para aquela janela).

## Rodar os scripts

Tudo que vem depois de `./run.sh` (ou `.\run.ps1`) é executado dentro do container:

```bash
./run.sh                                                # pipeline, 20 imagens por pasta (teste)
./run.sh scripts/aplicacaoModelov5-fast+rodosol.py      # pipeline completo
./run.sh scripts/comprovacaoAcertos.py --saida saidas   # mede os acertos
./run.sh scripts/consumidor_teste.py                    # confere o Kafka
```

Os resultados aparecem na pasta `saidas/` do seu computador.

## Vídeos

Os vídeos ficam na pasta `videos/` do projeto (criada pelo `run.sh`/`run.ps1`; não vai para o Git).

```bash
# baixar um vídeo (ex.: do YouTube) para videos/
./run.sh scripts/baixar_video.py "https://www.youtube.com/watch?v=..." --nome portao

# processar o vídeo inteiro, ou só um trecho (segundos ou minutos:segundos)
./run.sh scripts/aplicacaoModelov5-fast+rodosol.py --video videos/portao.mp4
./run.sh scripts/aplicacaoModelov5-fast+rodosol.py --video videos/portao.mp4 --inicio 1:30 --fim 2:10
```

O resultado vai para `saidas/portao/` (ou `saidas/portao_90-130s/` com trecho). Coloque o link entre aspas. As opções de vídeo (`VIDEO_*` no `.env`) estão explicadas no `.env.example` e no `README.md`.

## Dataset

O pipeline precisa da pasta `datasetConvertidoYOLO/` na raiz do projeto. Ela **não vem no Git**: peça a alguém da equipe ou gere com `converterDataset-YOLO.py`. Os comandos `build` e `--exemplo` funcionam sem ela.

## Cuidados

- No `.env`, escreva os valores **sem aspas** e com **caminhos relativos** (ex.: `PASTA_SAIDA=saidas`).
- Alterou um script ou o `requirements.txt`? Rode `./run.sh build` de novo.
- No `--video`, use barras normais (`videos/portao.mp4`), também no PowerShell: o caminho é lido dentro do container.
- Linux: se `saidas/` ficar com dono `root`, use `sudo chown -R $USER:$USER saidas`.
- Linux: `permission denied` ao usar `docker` significa que falta o passo 1 e sair/entrar na sessão.
- Windows: erro `$'\r': command not found` no `run.sh` é quebra de linha do Windows. Corrija com `dos2unix run.sh`.
- Ao publicar no Kafka, use `-n` pequeno e não deixe produtores ou consumidores rodando em segundo plano. Pare a ponte (Ctrl+C) quando terminar.
- Erro `localhost:9092 ... Connection refused` ao publicar: a ponte não está rodando ou o container não usou `--network container:ponte`.
- Docker no Windows sem alcançar o broker (`nc ... timed out` num container): se existir uma rede Docker na faixa `172.18.0.0/16` (por exemplo `a8_default`), remova-a com `docker network rm`.

## Publicar no Kafka da Jetson

O broker fica na rede do Dcomp: `172.18.4.56:9092` por dentro, `200.17.66.56:9092` por fora (ponha o seu no `KAFKA_BROKER` do `.env`). Ele ainda anuncia `localhost:9092`, então é preciso uma **ponte** em outro terminal e rodar o container na rede dela. O `run.sh` e o `run.ps1` não fazem isso.

```bash
# Terminal 1 (fica aberto; Ctrl+C no fim). Troque <ip> pelo endereço que vale para você.
docker rm -f ponte
docker run --rm --name ponte alpine/socat TCP-LISTEN:9092,fork,reuseaddr TCP:<ip>:9092
```

```bash
# Terminal 2, na pasta do projeto (Linux / Git Bash). Use -n pequeno.
docker run --rm --env-file .env --network container:ponte \
  -v "$(pwd)/datasetConvertidoYOLO:/app/datasetConvertidoYOLO" \
  -v "$(pwd)/saidas:/app/saidas" \
  -v "$(pwd)/videos:/app/videos" \
  -v i7-ocr-cache:/root/.cache/fast-plate-ocr \
  parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py -n 2

# Conferir (só o que ainda não foi lido por este consumidor):
docker run --rm --env-file .env --network container:ponte parkflow-i7 scripts/consumidor_teste.py --novas
```

No PowerShell, troque `\` por `` ` `` e `$(pwd)` por `${PWD}`. O `.env` precisa de `PUBLICACAO_MODO=kafka`. Detalhes e outros modos do consumidor (`--ao-vivo`) no `README.md`.

## Comando completo (sem o `run.sh`)

```bash
# Linux / Git Bash
docker run --rm --env-file .env \
  -v "$(pwd)/datasetConvertidoYOLO:/app/datasetConvertidoYOLO" \
  -v "$(pwd)/saidas:/app/saidas" \
  -v "$(pwd)/videos:/app/videos" \
  -v i7-ocr-cache:/root/.cache/fast-plate-ocr \
  parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py -n 20
```

```powershell
# PowerShell
docker run --rm --env-file .env `
  -v "${PWD}/datasetConvertidoYOLO:/app/datasetConvertidoYOLO" `
  -v "${PWD}/saidas:/app/saidas" `
  -v "${PWD}/videos:/app/videos" `
  -v i7-ocr-cache:/root/.cache/fast-plate-ocr `
  parkflow-i7 scripts/aplicacaoModelov5-fast+rodosol.py -n 20
```