"""
Consumidor de verificação do I7: lê os eventos do tópico e mostra o que um componente
consumidor (por exemplo, o app) enxergaria. Segue o guia do Kafka (B2, seções 4.2 e 4.3).

Serve para conferir DEPOIS de publicar: os eventos chegaram? O envelope passa na validação
do guia? Não altera nada no tópico.

Cuidados com o broker compartilhado (guia, seção 4.5):
  - o poll é de 1 s e o programa PARA sozinho (sem mensagem nova por --espera segundos, ou
    ao atingir --max mensagens). Não deixe rodando em segundo plano;
  - não grava posição de leitura (enable.auto.commit=False), então cada execução relê o
    tópico desde o início, sem atrapalhar o group.id de outro componente.

Uso:
    python scripts/consumidor_teste.py
    python scripts/consumidor_teste.py --max 10 --espera 15

O endereço do broker e o tópico vêm do .env (KAFKA_BROKER, KAFKA_TOPICO).
"""
import argparse
import json
import logging
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import publicador_eventos as pe

GROUP_ID = "I7-verificacao"   # só deste script; o app usa o group.id dele

# --- Validação do guia B2, seção 4.3 (o que um consumidor típico confere) ---------------
CAMPOS_OBRIGATORIOS = ["message_id", "message_type", "schema", "schema_version",
                       "producer", "occurred_at", "payload"]
VERSAO_SUPORTADA = "1."


def validar_envelope_guia(evento, schema_esperado):
    """Devolve (True, None) se o envelope passa na validação do guia, ou (False, motivo)."""
    for campo in CAMPOS_OBRIGATORIOS:
        if campo not in evento:
            return False, "campo obrigatório ausente: " + campo
    if evento["schema"] != schema_esperado:
        return False, "schema inesperado: " + str(evento["schema"])
    if not str(evento["schema_version"]).startswith(VERSAO_SUPORTADA):
        return False, "versão não suportada: " + str(evento["schema_version"])
    payload = evento["payload"]
    if not isinstance(payload, dict):
        return False, "payload não é um objeto"
    if "class" not in payload:
        return False, "payload sem o campo class"
    conf = payload.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        return False, "confidence ausente ou fora do intervalo 0 a 1"
    return True, None


def main():
    parser = argparse.ArgumentParser(description="Lê e valida os eventos do tópico do I7.")
    parser.add_argument("--max", type=int, default=50, help="para depois de ler N mensagens (padrão 50)")
    parser.add_argument("--espera", type=float, default=15,
                        help="para após N segundos sem mensagem nova (padrão 15)")
    args = parser.parse_args()

    try:
        from confluent_kafka import Consumer
    except ImportError:
        raise SystemExit("[ERRO] Instale a biblioteca: pip install confluent-kafka")

    topico = pe.topico()
    consumidor = Consumer({
        "bootstrap.servers": pe.broker(),
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",   # sem posição salva, começa da mensagem mais antiga
        "enable.auto.commit": False,       # não grava posição: a próxima execução relê tudo
    })
    consumidor.subscribe([topico])
    print(f"Lendo '{topico}' em {pe.broker()} (group.id={GROUP_ID}). "
          f"Para após {args.espera:g} s sem mensagens ou {args.max} lidas.\n")

    lidas = ok_guia = ok_i7 = descartadas = 0
    ultima = time.monotonic()
    try:
        while lidas < args.max and time.monotonic() - ultima < args.espera:
            msg = consumidor.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logging.warning("erro de leitura: %s", msg.error())
                continue
            ultima = time.monotonic()
            lidas += 1
            onde = f"p{msg.partition()}/off{msg.offset()}"
            try:
                evento = json.loads(msg.value().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                descartadas += 1
                print(f"[{onde}] DESCARTADA: JSON inválido")
                continue
            valido, motivo = validar_envelope_guia(evento, topico)
            if not valido:
                descartadas += 1
                print(f"[{onde}] DESCARTADA pelo guia: {motivo}")
                continue
            ok_guia += 1
            erros_i7 = pe.validar_evento(evento)   # contrato completo da I7 (mais rígido)
            ok_i7 += not erros_i7
            p = evento["payload"]
            print(f"[{onde}] {p.get('license_plate', '?'):8} {p['class']:10} "
                  f"conf={p['confidence']:.3f} seq={evento.get('sequence', '?')} "
                  f"src={evento.get('source_id', '?')} sessão={str(evento.get('session_id', '?'))[:8]}"
                  + ("" if not erros_i7 else f"  | contrato I7: {'; '.join(erros_i7[:2])}"))
    finally:
        consumidor.close()

    print(f"\n{lidas} mensagem(ns) lida(s): {ok_guia} passam na validação do guia, "
          f"{ok_i7} no contrato completo da I7, {descartadas} descartada(s).")


if __name__ == "__main__":
    main()