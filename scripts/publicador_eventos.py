"""
Publicador de eventos do I7 (Fase A: sem rede).

Monta o evento `ods.i7.plate_read` exatamente como a Arquitetura 6c descreve (envelope comum
de perfil EV-SINAL + payload), valida o resultado contra o contrato e o "publica" gravando uma
mensagem JSON por linha (JSONL) em um arquivo. Nenhuma conexão de rede é feita.

A Fase B acrescentará um publicador Kafka com a MESMA interface (publicar / fechar), então o
restante do código não precisará mudar: basta trocar o modo em `criar_publicador`.

Campos ainda pendentes (decisão do grupo): `character_confidences` e `uncertain` são
OBRIGATÓRIOS no contrato, mas por enquanto não são produzidos. Há duas saídas:
    - deixar de fora (padrão): o validador os trata como opcionais;
    - mockar (mockar_pendentes=True): preenche com valores FALSOS, só para testar o fluxo.
      Nunca use esses valores como se fossem medições reais.

Só usa a biblioteca padrão do Python.

Uso rápido (sem rodar nenhum modelo):
    python publicador_eventos.py --exemplo            # imprime um evento de exemplo e o valida
    python publicador_eventos.py --validar eventos.jsonl   # valida um arquivo JSONL existente
"""
import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ============================ CONTRATO (Arquitetura 6c) ============================
# Os nomes abaixo seguem a Arquitetura 6c. O guia do Kafka mostra um exemplo simplificado
# com `occurred_at`; a arquitetura usa `published_at` + `captured_at`. Se o TRA-1 mandar
# mudar, é só ajustar aqui e em CAMPOS_ENVELOPE.
SCHEMA = "ods.i7.plate_read"          # nome do contrato = nome do tópico
SCHEMA_VERSION = "1.0"
PRODUCER = "I7"

CAMPOS_ENVELOPE = (  # perfil EV-SINAL
    "message_type", "message_id", "schema", "schema_version", "producer", "published_at",
    "captured_at", "source_id", "session_id", "sequence", "artifact_version", "payload",
)
CAMPOS_ENVELOPE_OPCIONAIS = ("correlation_id",)

PAYLOAD_OBRIGATORIOS = ("frame_index", "class", "plate_layout", "license_plate", "confidence", "bbox")
PAYLOAD_OPCIONAIS = ("detection_id",)
PAYLOAD_PENDENTES = ("character_confidences", "uncertain")  # obrigatórios no contrato, ainda não produzidos

CLASSES_VEICULO = ("car", "motorcycle")
LAYOUTS = {
    "MERCOSUL": re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$"),   # AAA9A99
    "OLD_BR":   re.compile(r"^[A-Z]{3}[0-9]{4}$"),             # AAA9999
}
TAMANHO_PLACA = 7
# ==================================================================================


# ------------------------------ UTILITÁRIOS ------------------------------------------
def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def formatar_instante(dt: datetime) -> str:
    """ISO 8601 em UTC com milissegundos e 'Z' (ex.: 2026-09-23T14:30:00.120Z)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ler_instante(texto) -> datetime | None:
    """Lê um instante no formato acima. Devolve None se não for válido."""
    if not isinstance(texto, str) or not texto.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(texto[:-1] + "+00:00")
    except ValueError:
        return None


def _numero(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _inteiro(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _uuid_valido(v) -> bool:
    try:
        return isinstance(v, str) and str(uuid.UUID(v)) == v.lower()
    except ValueError:
        return False


# ------------------------------ VALIDAÇÃO ---------------------------------------------
def validar_evento(evento, exigir_pendentes: bool = False) -> list[str]:
    """
    Confere o evento contra o contrato. Devolve a lista de problemas (vazia = válido).
    `exigir_pendentes=True` torna obrigatórios character_confidences e uncertain.
    """
    if not isinstance(evento, dict):
        return ["o evento não é um objeto JSON"]
    erros = []

    # --- envelope ---
    for campo in CAMPOS_ENVELOPE:
        if campo not in evento:
            erros.append(f"campo obrigatório ausente no envelope: {campo}")
    for campo in evento:
        if campo not in CAMPOS_ENVELOPE and campo not in CAMPOS_ENVELOPE_OPCIONAIS:
            erros.append(f"campo desconhecido no envelope: {campo}")

    if evento.get("message_type") != "event":
        erros.append("message_type deve ser 'event'")
    if "message_id" in evento and not _uuid_valido(evento["message_id"]):
        erros.append("message_id não é um UUID válido")
    if "session_id" in evento and not _uuid_valido(evento["session_id"]):
        erros.append("session_id não é um UUID válido")
    if "schema" in evento and evento["schema"] != SCHEMA:
        erros.append(f"schema deve ser '{SCHEMA}'")
    if "schema_version" in evento and not re.fullmatch(r"\d+\.\d+", str(evento["schema_version"])):
        erros.append("schema_version deve ter o formato MAJOR.MINOR")
    if "producer" in evento and evento["producer"] != PRODUCER:
        erros.append(f"producer deve ser '{PRODUCER}'")
    for campo in ("source_id", "artifact_version"):
        if campo in evento and not (isinstance(evento[campo], str) and evento[campo].strip()):
            erros.append(f"{campo} deve ser um texto não vazio")
    if "sequence" in evento and not (_inteiro(evento["sequence"]) and evento["sequence"] >= 0):
        erros.append("sequence deve ser um inteiro >= 0")

    pub = cap = None
    for campo in ("published_at", "captured_at"):
        if campo in evento:
            dt = ler_instante(evento[campo])
            if dt is None:
                erros.append(f"{campo} deve ser ISO 8601 em UTC terminando em 'Z'")
            elif campo == "published_at":
                pub = dt
            else:
                cap = dt
    if pub and cap and cap > pub:
        erros.append("captured_at não pode ser depois de published_at")

    # --- payload ---
    payload = evento.get("payload")
    if "payload" in evento and not isinstance(payload, dict):
        erros.append("payload deve ser um objeto")
        return erros
    if payload is None:
        return erros

    obrigatorios = PAYLOAD_OBRIGATORIOS + (PAYLOAD_PENDENTES if exigir_pendentes else ())
    for campo in obrigatorios:
        if campo not in payload:
            erros.append(f"campo obrigatório ausente no payload: {campo}")
    permitidos = PAYLOAD_OBRIGATORIOS + PAYLOAD_OPCIONAIS + PAYLOAD_PENDENTES
    for campo in payload:
        if campo not in permitidos:
            erros.append(f"campo desconhecido no payload: {campo}")

    if "frame_index" in payload and not (_inteiro(payload["frame_index"]) and payload["frame_index"] >= 0):
        erros.append("frame_index deve ser um inteiro >= 0")
    if "detection_id" in payload and not _inteiro(payload["detection_id"]):
        erros.append("detection_id deve ser um inteiro")
    if "class" in payload and payload["class"] not in CLASSES_VEICULO:
        erros.append(f"class deve ser um de {CLASSES_VEICULO}")

    layout = payload.get("plate_layout")
    if "plate_layout" in payload and layout not in LAYOUTS:
        erros.append(f"plate_layout deve ser um de {tuple(LAYOUTS)}")
    if "license_plate" in payload:
        placa = payload["license_plate"]
        if not isinstance(placa, str) or len(placa) != TAMANHO_PLACA or placa != placa.upper() \
                or not placa.isalnum():
            erros.append(f"license_plate deve ter {TAMANHO_PLACA} caracteres maiúsculos, sem separadores")
        elif layout in LAYOUTS and not LAYOUTS[layout].match(placa):
            erros.append(f"license_plate '{placa}' não segue o formato de {layout}")

    if "confidence" in payload:
        c = payload["confidence"]
        if not (_numero(c) and 0 <= c <= 1):
            erros.append("confidence deve ser um número entre 0 e 1")

    if "bbox" in payload:
        b = payload["bbox"]
        if not (isinstance(b, list) and len(b) == 4 and all(_numero(v) and v >= 0 for v in b)):
            erros.append("bbox deve ser [x_min, y_min, x_max, y_max], números >= 0")
        elif not (b[0] < b[2] and b[1] < b[3]):
            erros.append("bbox deve ter x_min < x_max e y_min < y_max")

    if "character_confidences" in payload:
        cc = payload["character_confidences"]
        if not (isinstance(cc, list) and len(cc) == TAMANHO_PLACA
                and all(_numero(v) and 0 <= v <= 1 for v in cc)):
            erros.append(f"character_confidences deve ter {TAMANHO_PLACA} números entre 0 e 1")
    if "uncertain" in payload and not isinstance(payload["uncertain"], bool):
        erros.append("uncertain deve ser true ou false")

    return erros


# ------------------------------ CONSTRUÇÃO --------------------------------------------
class ConstrutorEventos:
    """
    Monta eventos `ods.i7.plate_read`. Guarda o estado que o envelope exige:
    um `session_id` por execução e um contador `sequence` que começa em 0.

    Observação: no sistema completo, o session_id vem da fonte de vídeo (P4, via
    ods.session.started). Enquanto o P4 não está integrado, a sessão é gerada aqui.
    """

    def __init__(self, source_id: str, artifact_version: str, mockar_pendentes: bool = False):
        self.source_id = source_id
        self.artifact_version = artifact_version
        self.mockar_pendentes = mockar_pendentes
        self.session_id = str(uuid.uuid4())
        self.sequence = 0

    def plate_read(self, *, placa: str, plate_layout: str, classe_veiculo: str, confianca: float,
                   bbox, frame_index: int, captured_at: datetime | None = None) -> dict:
        publicado = agora_utc()
        capturado = captured_at or publicado

        payload = {
            "frame_index": int(frame_index),
            "class": classe_veiculo,
            "plate_layout": plate_layout,
            "license_plate": placa,
            "confidence": round(float(confianca), 4),
            "bbox": [int(v) for v in bbox],
        }
        if self.mockar_pendentes:  # VALORES FALSOS, só para exercitar o fluxo
            payload["character_confidences"] = [payload["confidence"]] * TAMANHO_PLACA
            payload["uncertain"] = False

        evento = {
            "message_type": "event",
            "message_id": str(uuid.uuid4()),
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "producer": PRODUCER,
            "published_at": formatar_instante(publicado),
            "captured_at": formatar_instante(capturado),
            "source_id": self.source_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "artifact_version": self.artifact_version,
            "payload": payload,
        }
        self.sequence += 1
        return evento


# ------------------------------ PUBLICADORES ------------------------------------------
class PublicadorNulo:
    """Modo 'desligado': não gera nem grava nada."""
    ativo = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def publicar(self, evento) -> bool:
        return False

    def resumo(self) -> str:
        return "publicação desligada"


class PublicadorArquivo:
    """Modo 'arquivo' (Fase A): valida e grava um evento JSON por linha. Sem rede."""
    ativo = True

    def __init__(self, caminho, exigir_pendentes: bool = False):
        self.caminho = Path(caminho)
        self.exigir_pendentes = exigir_pendentes
        self.publicados = 0
        self.descartados = 0
        self._arquivo = None

    def __enter__(self):
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo = open(self.caminho, "w", encoding="utf-8")
        return self

    def __exit__(self, *args):
        self.fechar()
        return False

    def publicar(self, evento) -> bool:
        """Grava o evento se for válido. Devolve True se gravou, False se descartou."""
        erros = validar_evento(evento, self.exigir_pendentes)
        if erros:
            self.descartados += 1
            print(f"[PUBLICAÇÃO] evento descartado: {'; '.join(erros[:3])}")
            return False
        self._arquivo.write(json.dumps(evento, ensure_ascii=False) + "\n")
        self.publicados += 1
        return True

    def fechar(self):
        if self._arquivo and not self._arquivo.closed:
            self._arquivo.close()

    def resumo(self) -> str:
        return (f"{self.publicados} evento(s) gravado(s) em {self.caminho}"
                + (f", {self.descartados} descartado(s) por violar o contrato" if self.descartados else ""))


def criar_publicador(modo: str, caminho_arquivo, exigir_pendentes: bool = False):
    modo = (modo or "").strip().lower()
    if modo == "desligado":
        return PublicadorNulo()
    if modo == "arquivo":
        return PublicadorArquivo(caminho_arquivo, exigir_pendentes)
    if modo == "kafka":
        raise NotImplementedError("O modo 'kafka' será implementado na Fase B. Use 'arquivo' ou 'desligado'.")
    raise ValueError(f"PUBLICACAO_MODO inválido: '{modo}'. Use 'arquivo' ou 'desligado'.")


# ------------------------------ LINHA DE COMANDO --------------------------------------
def _validar_arquivo(caminho, exigir_pendentes: bool) -> int:
    caminho = Path(caminho)
    if not caminho.is_file():
        print(f"[ERRO] Arquivo não encontrado: {caminho}")
        return 2
    total = invalidos = 0
    with open(caminho, "r", encoding="utf-8") as f:
        for n, linha in enumerate(f, 1):
            if not linha.strip():
                continue
            total += 1
            try:
                evento = json.loads(linha)
            except json.JSONDecodeError as e:
                erros = [f"JSON inválido ({e.msg})"]
            else:
                erros = validar_evento(evento, exigir_pendentes)
            if erros:
                invalidos += 1
                print(f"linha {n}: " + "; ".join(erros))
    print(f"{total} evento(s) lido(s), {total - invalidos} válido(s), {invalidos} inválido(s).")
    return 1 if invalidos else 0


def _main():
    parser = argparse.ArgumentParser(description="Utilitários do publicador de eventos do I7.")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--exemplo", action="store_true", help="imprime um evento de exemplo e o valida")
    grupo.add_argument("--validar", metavar="ARQUIVO", help="valida um arquivo JSONL de eventos")
    parser.add_argument("--exigir-pendentes", action="store_true",
                        help="trata character_confidences e uncertain como obrigatórios")
    args = parser.parse_args()

    if args.exemplo:
        c = ConstrutorEventos("cam-entrada", "exemplo-0.0.0", mockar_pendentes=args.exigir_pendentes)
        evento = c.plate_read(placa="BRA2E19", plate_layout="MERCOSUL", classe_veiculo="car",
                              confianca=0.94, bbox=[1020, 640, 1190, 700], frame_index=0)
        print(json.dumps(evento, ensure_ascii=False, indent=2))
        erros = validar_evento(evento, args.exigir_pendentes)
        print("\nVálido." if not erros else "\nProblemas: " + "; ".join(erros))
        return 0 if not erros else 1
    return _validar_arquivo(args.validar, args.exigir_pendentes)


if __name__ == "__main__":
    raise SystemExit(_main())