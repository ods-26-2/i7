"""
Publicador de eventos do I7 (modos: arquivo, kafka, desligado).

Monta o evento `ods.i7.plate_read` exatamente como a Arquitetura 6c descreve (envelope comum
de perfil EV-SINAL + payload), valida o resultado contra o contrato e o "publica" gravando uma
mensagem JSON por linha (JSONL) em um arquivo. Nenhuma conexão de rede é feita.

O publicador Kafka (modo 'kafka') tem a MESMA interface (publicar / fechar), então o restante
do código não muda: basta trocar PUBLICACAO_MODO no .env.

Payload: além da placa lida, o evento leva `character_confidences` (confiança de cada um dos 7
caracteres), `uncertain` (leitura incerta: confiança mínima abaixo do limiar) e `detection_id`
(índice da detecção dentro do quadro; GENÉRICO por enquanto: ainda não acompanha o veículo entre
quadros, então não identifica o mesmo veículo em quadros diferentes).

Só usa a biblioteca padrão do Python.

Uso rápido (sem rodar nenhum modelo):
    python publicador_eventos.py --exemplo            # imprime um evento de exemplo e o valida
    python publicador_eventos.py --validar eventos.jsonl   # valida um arquivo JSONL existente
"""
import argparse
import atexit
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ============================ CONTRATO (Arquitetura 6c) ============================
# Os nomes abaixo seguem a Arquitetura 6c, mais `occurred_at`, que o guia do Kafka (B2) exige
# de todo consumidor. `occurred_at` recebe o mesmo valor de `captured_at` (quando o fato
# ocorreu); `published_at` e `captured_at` continuam no envelope como campos extras.
# O nome do contrato é o nome do tópico (guia B2, seção 3) e vem da variável KAFKA_TOPICO.
TOPICO_PADRAO = "ods.i7.plate_read"
SCHEMA_VERSION = "1.0"   # versão do contrato da Especificação de interfaces 1.0 (arquitetura 6c)
PRODUCER = "I7"

CAMPOS_ENVELOPE = (  # perfil EV-SINAL
    "message_type", "message_id", "schema", "schema_version", "producer", "occurred_at",
    "published_at", "captured_at", "source_id", "session_id", "sequence", "artifact_version", "payload",
)
CAMPOS_ENVELOPE_OPCIONAIS = ("correlation_id",)

PAYLOAD_OBRIGATORIOS = (
    "frame_index", "detection_id", "class", "plate_layout", "license_plate", "confidence",
    "character_confidences", "uncertain", "bbox",
)

CLASSES_VEICULO = ("car", "motorcycle")
LAYOUTS = {
    "MERCOSUL": re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$"),   # AAA9A99
    "OLD_BR":   re.compile(r"^[A-Z]{3}[0-9]{4}$"),             # AAA9999
}
TAMANHO_PLACA = 7
# ==================================================================================


# ------------------------------ UTILITÁRIOS ------------------------------------------
def topico() -> str:
    """Nome do tópico = valor do campo `schema`. Lido na hora do uso (e não ao importar),
    porque o .env só é carregado depois que este módulo é importado."""
    return os.environ.get("KAFKA_TOPICO", "").strip() or TOPICO_PADRAO


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
def validar_evento(evento) -> list[str]:
    """
    Confere o evento contra o contrato. Devolve a lista de problemas (vazia = válido).
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
    if "schema" in evento and evento["schema"] != topico():
        erros.append(f"schema deve ser '{topico()}'")
    if "schema_version" in evento and not re.fullmatch(r"\d+\.\d+", str(evento["schema_version"])):
        erros.append("schema_version deve ter o formato MAJOR.MINOR")
    if "producer" in evento and evento["producer"] != PRODUCER:
        erros.append(f"producer deve ser '{PRODUCER}'")
    for campo in ("source_id", "artifact_version"):
        if campo in evento and not (isinstance(evento[campo], str) and evento[campo].strip()):
            erros.append(f"{campo} deve ser um texto não vazio")
    if "sequence" in evento and not (_inteiro(evento["sequence"]) and evento["sequence"] >= 0):
        erros.append("sequence deve ser um inteiro >= 0")

    pub = cap = occ = None
    for campo in ("occurred_at", "published_at", "captured_at"):
        if campo in evento:
            dt = ler_instante(evento[campo])
            if dt is None:
                erros.append(f"{campo} deve ser ISO 8601 em UTC terminando em 'Z'")
            elif campo == "published_at":
                pub = dt
            elif campo == "captured_at":
                cap = dt
            else:
                occ = dt
    if pub and cap and cap > pub:
        erros.append("captured_at não pode ser depois de published_at")
    if occ and cap and occ != cap:
        erros.append("occurred_at deve ser igual a captured_at")

    # --- payload ---
    payload = evento.get("payload")
    if "payload" in evento and not isinstance(payload, dict):
        erros.append("payload deve ser um objeto")
        return erros
    if payload is None:
        return erros

    for campo in PAYLOAD_OBRIGATORIOS:
        if campo not in payload:
            erros.append(f"campo obrigatório ausente no payload: {campo}")
    for campo in payload:
        if campo not in PAYLOAD_OBRIGATORIOS:
            erros.append(f"campo desconhecido no payload: {campo}")

    if "frame_index" in payload and not (_inteiro(payload["frame_index"]) and payload["frame_index"] >= 0):
        erros.append("frame_index deve ser um inteiro >= 0")
    if "detection_id" in payload and not (_inteiro(payload["detection_id"]) and payload["detection_id"] >= 0):
        erros.append("detection_id deve ser um inteiro >= 0")
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

    def __init__(self, source_id: str, artifact_version: str):
        self.source_id = source_id
        self.artifact_version = artifact_version
        self.session_id = str(uuid.uuid4())
        self.sequence = 0

    def plate_read(self, *, placa: str, plate_layout: str, classe_veiculo: str, confianca: float,
                   character_confidences, uncertain: bool, bbox, frame_index: int,
                   detection_id: int = 0, captured_at: datetime | None = None) -> dict:
        publicado = agora_utc()
        capturado = captured_at or publicado

        payload = {
            "frame_index": int(frame_index),
            "detection_id": int(detection_id),   # índice da detecção no quadro (genérico por enquanto)
            "class": classe_veiculo,
            "plate_layout": plate_layout,
            "license_plate": placa,
            "confidence": round(float(confianca), 4),
            "character_confidences": [round(float(c), 4) for c in character_confidences],
            "uncertain": bool(uncertain),
            "bbox": [int(v) for v in bbox],
        }

        evento = {
            "message_type": "event",
            "message_id": str(uuid.uuid4()),
            "schema": topico(),
            "schema_version": SCHEMA_VERSION,
            "producer": PRODUCER,
            "occurred_at": formatar_instante(capturado),
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

    def __init__(self, caminho):
        self.caminho = Path(caminho)
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
        erros = validar_evento(evento)
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


def broker() -> str:
    """Endereço do broker (guia B2: configurável, nunca fixo no código)."""
    return os.environ.get("KAFKA_BROKER", "").strip() or "34.66.235.91:9093"


_PRODUCER = None   # um único produtor por execução: evita reconectar ao broker a cada pasta


def _flush_final():
    if _PRODUCER is not None:
        _PRODUCER.flush(20)


atexit.register(_flush_final)


class PublicadorKafka:
    """
    Modo 'kafka' (Fase B): valida cada evento e o envia ao tópico `topico()` do broker `broker()`.
    Mesma interface do PublicadorArquivo (publicar / fechar / resumo), então o restante do
    código não muda.

    Cuidados com o broker compartilhado (guia B2, seção 4.5): sem laços de repetição, sem
    reconexões infinitas (message.timeout.ms limita o tempo de cada envio) e flush() ao fechar.
    A chave da mensagem é o source_id, para manter a ordem dos eventos de uma mesma câmera.
    """
    ativo = True

    def __init__(self):
        self.topico = topico()
        self.broker = broker()
        self.enviados = 0       # entregues ao broker (confirmação recebida)
        self.falhas = 0         # o broker não confirmou
        self.descartados = 0    # violaram o contrato, nem foram enviados
        self._producer = None

    def __enter__(self):
        try:
            from confluent_kafka import Producer
        except ImportError:
            raise SystemExit("[ERRO] Modo 'kafka' exige: pip install confluent-kafka")
        global _PRODUCER
        if _PRODUCER is None:
            _PRODUCER = Producer({
                "bootstrap.servers": self.broker,
                "client.id": "i7-publicador",
                "acks": "all",
                "enable.idempotence": True,        # reenvios internos não duplicam mensagens
                "message.timeout.ms": 15000,       # desiste de uma mensagem após 15 s
            })
        self._producer = _PRODUCER
        return self

    def __exit__(self, *args):
        self.fechar()
        return False

    def _confirmacao(self, erro, _msg):
        if erro is None:
            self.enviados += 1
        else:
            self.falhas += 1
            if self.falhas <= 3:  # evita inundar o terminal
                print(f"[PUBLICAÇÃO] falha ao entregar ao Kafka: {erro}")

    def publicar(self, evento) -> bool:
        """Valida e enfileira o envio. Devolve True se enfileirou (a entrega é confirmada depois)."""
        erros = validar_evento(evento)
        if erros:
            self.descartados += 1
            print(f"[PUBLICAÇÃO] evento descartado: {'; '.join(erros[:3])}")
            return False
        dados = json.dumps(evento, ensure_ascii=False).encode("utf-8")
        chave = evento["source_id"].encode("utf-8")
        try:
            self._producer.produce(self.topico, value=dados, key=chave, on_delivery=self._confirmacao)
        except BufferError:   # fila interna cheia: espera um pouco UMA vez e tenta de novo
            self._producer.poll(1.0)
            try:
                self._producer.produce(self.topico, value=dados, key=chave, on_delivery=self._confirmacao)
            except BufferError:
                self.falhas += 1
                return False
        self._producer.poll(0)   # atende as confirmações já recebidas
        return True

    def fechar(self):
        if self._producer is not None:
            # Espera até 20 s pelas confirmações. O produtor continua aberto para a próxima
            # pasta; ele é esvaziado de novo e encerrado ao fim do programa (atexit).
            restantes = self._producer.flush(20)
            if restantes:
                self.falhas += restantes
                print(f"[PUBLICAÇÃO] {restantes} mensagem(ns) sem confirmação do broker")
            self._producer = None

    def resumo(self) -> str:
        return (f"{self.enviados} evento(s) entregue(s) ao tópico {self.topico} ({self.broker})"
                + (f", {self.falhas} falha(s) de entrega" if self.falhas else "")
                + (f", {self.descartados} descartado(s) por violar o contrato" if self.descartados else ""))


def criar_publicador(modo: str, caminho_arquivo):
    modo = (modo or "").strip().lower()
    if modo == "desligado":
        return PublicadorNulo()
    if modo == "arquivo":
        return PublicadorArquivo(caminho_arquivo)
    if modo == "kafka":
        return PublicadorKafka()
    raise ValueError(f"PUBLICACAO_MODO inválido: '{modo}'. Use 'arquivo', 'kafka' ou 'desligado'.")


# ------------------------------ LINHA DE COMANDO --------------------------------------
def _validar_arquivo(caminho) -> int:
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
                erros = validar_evento(evento)
            if erros:
                invalidos += 1
                print(f"linha {n}: " + "; ".join(erros))
    print(f"{total} evento(s) lido(s), {total - invalidos} válido(s), {invalidos} inválido(s).")
    return 1 if invalidos else 0


def _criar_topico() -> int:
    """Cria o tópico no broker se ele ainda não existir. Uma consulta + uma criação, sem loop."""
    try:
        from confluent_kafka.admin import AdminClient, NewTopic
    except ImportError:
        print("[ERRO] Instale a biblioteca: pip install confluent-kafka")
        return 2
    nome = topico()
    admin = AdminClient({"bootstrap.servers": broker()})
    if nome in admin.list_topics(timeout=10).topics:
        print(f"O tópico '{nome}' já existe em {broker()}. Nada a fazer.")
        return 0
    # 1 partição: preserva a ordem dos eventos (campo sequence). O cluster tem 1 broker.
    futuros = admin.create_topics([NewTopic(nome, num_partitions=1, replication_factor=1)])
    try:
        futuros[nome].result(timeout=15)
    except Exception as e:  # o motivo (permissão, política do broker...) vem na mensagem
        print(f"[ERRO] Não foi possível criar '{nome}': {e}")
        return 1
    print(f"Tópico '{nome}' criado em {broker()}.")
    return 0


def _main():
    try:  # lê o .env da raiz do projeto, se python-dotenv estiver instalado
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description="Utilitários do publicador de eventos do I7.")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--exemplo", action="store_true", help="imprime um evento de exemplo e o valida")
    grupo.add_argument("--validar", metavar="ARQUIVO", help="valida um arquivo JSONL de eventos")
    grupo.add_argument("--criar-topico", action="store_true",
                       help="cria o tópico KAFKA_TOPICO no broker KAFKA_BROKER (se não existir)")
    args = parser.parse_args()

    if args.criar_topico:
        return _criar_topico()
    if args.exemplo:
        c = ConstrutorEventos("cam-entrada", "exemplo-0.0.0")
        confs = [0.99, 0.98, 0.97, 0.95, 0.94, 0.96, 0.98]
        evento = c.plate_read(placa="BRA2E19", plate_layout="MERCOSUL", classe_veiculo="car",
                              confianca=min(confs), character_confidences=confs, uncertain=False,
                              bbox=[1020, 640, 1190, 700], frame_index=0, detection_id=0)
        print(json.dumps(evento, ensure_ascii=False, indent=2))
        erros = validar_evento(evento)
        print("\nVálido." if not erros else "\nProblemas: " + "; ".join(erros))
        return 0 if not erros else 1
    return _validar_arquivo(args.validar)


if __name__ == "__main__":
    raise SystemExit(_main())