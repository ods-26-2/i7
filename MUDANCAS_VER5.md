# ParkFlow · VER-5: diferenças em relação à arquitetura 6c

> Leia este documento antes do `README.md`. Ele resume, em poucos minutos, onde a nossa build (A8 + I7) **difere da arquitetura 6c e da Especificação de interfaces 1.0**, e como usar os arquivos.

**Squad VER-5:** Gabriel Vaz Bernardini Ferreira, Rebeca Martins Guimarães, Miguel de Assis Viveiros e Nicolas Rafael Mendonça Teles.

---

## 1. Escopo

A build é de **controle de acesso veicular por placa em condomínios e empresas**. O I7 lê a placa e publica `ods.i7.plate_read`; o A8 consome o evento, confere o cadastro e decide se libera.

| Na 6c (A8 · Estacionamento — vagas) | Na build VER-5 |
|---|---|
| Reserva por evento | **Não será feita** |
| Tarifa e pagamento | **Não será feita** |
| Mapa de vagas (artefato ZONES, uma zona por vaga) | **Não será feito**; item 20 da Especificação (ZONES e RULES) não se aplica |
| Ocupação de vagas (`ods.s1.zone_occupancy`) | **Não se aplica**; item 15 da Especificação |
| Referencial da instalação e cadastro multi-câmera (CALIBRATION de I3) | **Removido**, junto com as dependências do A8 em S1, CALIBRATION e ZONES (capacidades exclusivas de estacionamento) |
| Regra de entrada: vaga livre + reserva válida | **Substituída** por: veículo cadastrado (empresa) ou convidado dentro do período |

## 2. Entrada e saída

Na 6c, o I7 não informa se o veículo entra ou sai: o A8 decide a partir do `source_id` da leitura e das zonas de S1. **Mantivemos o princípio** (o I7 não publica movimento), mas **não usamos o S1 de forma alguma**. O A8 decide **somente pelo `source_id`**: `cam-entrada` aciona a cancela de entrada e `cam-saida`, a de saída.

## 3. Cancela (não existe na 6c)

A 6c não tem o componente que abre e fecha a cancela. Criamos um acionamento **genérico e simulado** dentro do A8:

- Só a leitura **liberada** aciona a cancela.
- Fecha sozinha **10 s** depois de abrir.
- Mesma placa na mesma câmera: não abre de novo em **30 s**.
- Evento com mais de **30 s** de atraso não abre nada.
- A cancela simulada só **escreve no log** (`[CANCELA] ABRIR` / `FECHAR`).

Por enquanto é só simulação (catraca virtual).

## 4. Payload do I7: 6c × nossa build

O **tópico** (`ods.i7.plate_read`), o **esquema** e a **`schema_version` 1.0** são os da 6c. O payload tem os mesmos campos e tipos da 6c; mudam o **significado** de alguns e a **origem** de outros:

| Campo | Na 6c | Na nossa build |
|---|---|---|
| `confidence` | Confiança global | **Menor** confiança entre os 7 caracteres (foi a medida que melhor separou leituras certas de erradas) |
| `character_confidences` | 7 valores, um por posição | Igual: 7 valores, na ordem da placa final |
| `uncertain` | `true` quando a validação de formato **corrigiu ou rejeitou** algum caractere | `true` quando a **menor confiança** fica abaixo de `LIMIAR_INCERTEZA` (padrão 0.9). **Ainda não marcamos correções do formato**; leituras com formato inválido são rejeitadas e **não geram evento** |
| `detection_id` | Detecção de **veículo** do I1 à qual a placa pertence (opcional, quando o I1 está na build) | Sem I1 na build: é só o **índice da placa no quadro** (0, 1, 2…). Passa a ter o sentido da 6c quando houver acompanhamento/votação |
| `class` | Vocabulário do I1 (`car`, `motorcycle`) | Mesmos valores, **inferidos** pelo número de linhas da placa (1 = `car`, 2 = `motorcycle`) |
| `frame_index` | Quadro do stream `ods.p4.frame` | Posição da imagem dentro da pasta processada (a stream de P4 ainda não está disponível) |
| `bbox` | Placa no quadro de P4 | Placa na imagem de entrada, em pixels (a stream de P4 ainda não está disponível) |
| `license_plate`, `plate_layout` | 7 caracteres; `OLD_BR` ou `MERCOSUL` | Iguais |

Diferenças no envelope e na origem dos dados:

- `artifact_version`: a 6c exemplifica `lpr-br-2.1.0`; usamos `<modelo YOLO>+<modelo de OCR>` (configurável por `ARTIFACT_VERSION`).
- `occurred_at`: **campo acrescentado ao envelope**, que não consta na 6c (ela usa `captured_at` e `published_at`). O guia do Kafka B2 o lista como campo **obrigatório** (`CAMPOS_OBRIGATORIOS`), e um consumidor no padrão do guia descarta a mensagem sem ele. Para o I7 é o mesmo instante de `captured_at` (quando o veículo foi visto), então os dois têm o mesmo valor. O `schema_version` continua `1.0`, pois o payload segue a 6c.
- `session_id`: o I7 cria uma sessão por execução. A 6c prevê `ods.session.started` publicado por P4, P5 e P6; como a stream de P4 ainda não está disponível, não consumimos nem publicamos esse marcador.
- O I7 lê **imagens do disco** (YOLO + OCR). Na 6c ele consome `ods.p4.frame` e usa a biblioteca de P2; usamos imagens do disco porque a stream de P4 ainda não está disponível.
- Como na 6c, o I7 **não grava evidência própria**.

Do lado do A8, o `uncertain` **ainda não é usado**: a decisão usa a `confidence` (mínimo 0,9), que dá o mesmo resultado hoje.

## 5. Como usar os arquivos

### Lado I7 (leitura de placas)

1. Copie `.env.example` para `.env` e ajuste os caminhos. O `.env` **não vai para o git**.
2. Teste sem broker: `PUBLICACAO_MODO=arquivo` e rode `python scripts/aplicacaoModelov5-fast+rodosol.py -n 2`. Os eventos ficam em `saidas/<pasta>/eventos.jsonl`.
3. Para publicar de verdade: `PUBLICACAO_MODO=kafka` e, na primeira vez, `python scripts/publicador_eventos.py --criar-topico`.
4. Para conferir o que chegou ao broker: `python scripts/consumidor_teste.py --max 60`.

> O broker é **temporário e compartilhado** (gasta créditos do projeto). Use sempre `-n` pequeno e não deixe processos rodando em laço.

### Lado A8 (aplicação)

1. No `backend/.env`: `KAFKA_ATIVO=true` (o consumidor fica desligado por padrão).
2. Suba com `docker compose up -d --build backend`. O `--build` é necessário, porque a imagem guarda uma cópia do código.
3. Acompanhe o log do backend: cada leitura mostra a decisão (liberado, negado ou baixa confiança) e, se liberada, `[CANCELA] ABRIR` e, 10 s depois, `FECHAR`.
4. Testes: `npm test` em `backend/` (38 testes).

Variáveis do A8: `KAFKA_ATIVO`, `KAFKA_BROKER`, `KAFKA_TOPICO`, `KAFKA_GROUP_ID`, `CANCELA_FECHAR_APOS_SEGUNDOS`, `CANCELA_JANELA_REPETICAO_SEGUNDOS`, `CANCELA_IDADE_MAXIMA_SEGUNDOS`. Detalhes em `docs/kafka.md` e `docs/cancela.md`.

## 6. Pendências

- Passar a consumir a stream `ods.p4.frame` quando ela estiver disponível.
- Fazer o `uncertain` também marcar caracteres corrigidos pela validação de formato (como na 6c).
- Acompanhar a mesma placa entre frames (votação) e dar ao `detection_id` o sentido da 6c.
- Incluir o CNPJ no evento e filtrar por empresa no A8 (hoje a placa é procurada em qualquer empresa).
- Trocar o broker temporário por um broker local.