# ParkFlow · VER-5: o que mudou em relação à arquitetura 6c

> Leia este documento antes do `README.md`. Ele resume, em poucos minutos, o que a nossa squad (A8 + I7) mudou ou deixou de fazer em relação à arquitetura 6c e à Especificação de interfaces 1.0, e como usar os arquivos.

**Squad VER-5:** Gabriel Vaz Bernardini Ferreira, Rebeca Martins Guimarães, Miguel de Assis Viveiros e Nicolas Rafael Mendonça Teles.

---

## 1. Escopo: controle de acesso por placa

A nossa build é de **controle de acesso veicular por placa em condomínios e empresas**.

- **I7** lê a placa na imagem e publica o resultado no Kafka.
- **A8** consome essa leitura, confere no cadastro (veículos de empresa, convidados temporários) e decide se libera.
- Se liberar, aciona a cancela (por enquanto **simulada**).

## 2. O que sai do escopo

| Item da arquitetura 6c / Especificação 1.0 | Situação na VER-5 |
|---|---|
| Reserva por evento, tarifa e pagamento, mapa de vagas | **Não serão feitos** |
| Item 15 da Especificação 1.0 (ocupação de vagas) | **Não se aplica** |
| Item 20 (artefatos ZONES e RULES) | **Não se aplica** |
| Dependências do A8 em S1, CALIBRATION e ZONES | **A revisar**: só ficam as que o controle de acesso precisa |

## 3. O que a arquitetura 6c não prevê (e fizemos de forma provisória)

### 3.1 Acionamento da cancela

A 6c não mostra o componente que abre e fecha a cancela. Criamos um **acionamento genérico e simulado** dentro do A8:

- A cancela é escolhida pelo **`source_id` da câmera** (`cam-entrada` abre a cancela de entrada, `cam-saida` a de saída). Não existe cadastro de portões.
- Só a leitura **liberada** aciona a cancela. Leitura negada ou de baixa confiança não aciona.
- A cancela **fecha sozinha 10 s depois** de abrir.
- A **mesma placa na mesma câmera** não abre de novo dentro de 30 s.
- Um evento com mais de 30 s de atraso não abre nada (proteção contra mensagens antigas).
- A cancela simulada só **escreve no log** (`[CANCELA] ABRIR` / `FECHAR`).

**Pendente:** definir o contrato e o squad responsável pelo acionamento real. Até lá, é só a simulação. Quando houver um protocolo definido, basta trocar o driver, sem mexer no resto.

### 3.2 Regras provisórias do cadastro

- A placa é procurada em **qualquer empresa**, porque o evento ainda não traz CNPJ. O filtro por CNPJ entra depois da entrega.
- O A8 **não grava as leituras** em nenhuma tabela. Não criamos tabelas novas.

### 3.3 Nome do tópico

Usamos `ods.i7.plate_read`, seguindo o padrão `ods.<componente>.<evento>` dos outros tópicos. O guia sugere `ods.veiculo.placa_lida`. Se a TRA-1 decidir trocar, basta mudar `KAFKA_TOPICO`.

## 4. O que mudou no payload

O evento `ods.i7.plate_read` continua com o mesmo envelope. Mudou o **payload** (versão do esquema **1.0 → 1.1**):

| Campo | Antes (1.0) | Agora (1.1) |
|---|---|---|
| `detection_id` | não existia | **novo.** Índice da detecção no frame (0, 1, 2…). Por enquanto é genérico; só passará a identificar o veículo quando houver acompanhamento entre frames |
| `character_confidences` | não existia | **novo.** Lista com a confiança de cada um dos 7 caracteres, na ordem da placa |
| `uncertain` | não existia | **novo.** `true` se a menor confiança ficou abaixo de `LIMIAR_INCERTEZA` (padrão 0.9) |
| `confidence` | confiança média | **menor** confiança entre os 7 caracteres. Foi a medida que melhor separou leituras certas de erradas |
| `license_plate`, `plate_layout`, `class`, `bbox`, `frame_index` | iguais | iguais |

Exemplo da versão 1.1:

```json
"payload": {
  "frame_index": 0, "detection_id": 0, "class": "motorcycle", "plate_layout": "OLD_BR",
  "license_plate": "MSA2398", "confidence": 0.9998,
  "character_confidences": [0.9999, 0.9998, 1.0, 1.0, 0.9999, 0.9998, 0.9998],
  "uncertain": false, "bbox": [699, 288, 793, 360]
}
```

Pontos importantes:

- A mudança **só acrescenta campos**. Quem lê a versão 1.x continua funcionando, e o A8 não precisou de alteração.
- Mensagens antigas que já estão no tópico **não têm** os campos novos. É normal o `consumidor_teste.py` apontar erro nelas.
- Só entram no tópico leituras cujo formato de placa foi validado. Leitura recusada não gera evento.

## 5. Como usar os arquivos

### Lado I7 (leitura de placas)

1. Copie `.env.example` para `.env` e ajuste os caminhos. O `.env` **não vai para o git**.
2. Teste sem broker: `PUBLICACAO_MODO=arquivo` e rode `python scripts/aplicacaoModelov5-fast+rodosol.py -n 2`. Os eventos ficam em `saidas/<pasta>/eventos.jsonl`.
3. Para publicar de verdade: `PUBLICACAO_MODO=kafka`, e na primeira vez `python scripts/publicador_eventos.py --criar-topico`.
4. Para conferir o que chegou ao broker: `python scripts/consumidor_teste.py --max 60`.

> O broker é **temporário e compartilhado** (gasta créditos do projeto). Use sempre `-n` pequeno e não deixe processos rodando em laço.

### Lado A8 (aplicação)

1. No `backend/.env`: `KAFKA_ATIVO=true` (o consumidor fica desligado por padrão).
2. Suba com `docker compose up -d --build backend`. O `--build` é necessário, porque a imagem guarda uma cópia do código.
3. Acompanhe o log do backend: cada leitura mostra a decisão (liberado, negado ou baixa confiança) e, quando liberada, `[CANCELA] ABRIR` e, 10 s depois, `FECHAR`.
4. Testes: `npm test` em `backend/` (38 testes).

Variáveis principais do A8: `KAFKA_ATIVO`, `KAFKA_BROKER`, `KAFKA_TOPICO`, `KAFKA_GROUP_ID`, `CANCELA_FECHAR_APOS_SEGUNDOS`, `CANCELA_JANELA_REPETICAO_SEGUNDOS`, `CANCELA_IDADE_MAXIMA_SEGUNDOS`. Detalhes em `docs/kafka.md` e `docs/cancela.md`.

## 6. Pendências

- Aprovar o contrato e o squad do acionamento real da cancela.
- Revisar as dependências do A8 em S1, CALIBRATION e ZONES.
- Incluir o CNPJ no evento e filtrar por empresa no A8.
- Acompanhar a mesma placa entre frames (votação) e preencher o `detection_id` de verdade.
- Trocar o broker temporário por um broker local.
