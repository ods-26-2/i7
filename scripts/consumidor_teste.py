"""
Consumidor de verificação do I7: lê os eventos do tópico e mostra o que um componente
consumidor (por exemplo, o app) enxergaria. Segue o guia do Kafka (B2, seções 4.2 e 4.3).

Serve para conferir DEPOIS de publicar: os eventos chegaram? O envelope passa na validação
do guia? Não altera nada no tópico.

Cuidados com o broker compartilhado (guia, seção 4.5):
  - o poll é de 1 s e o programa PARA sozinho (sem mensagem nova por --espera segundos, ou
    ao atingir --max mensagens). Não deixe rodando em segundo plano;
  - os group.id deste script são só dele (nunca o do app), então ler por aqui não atrapalha
    o funcionamento de outro componente.

Modos de leitura:
  (sem opção)  relê o tópico desde o início; não grava posição, então pode repetir à vontade
  --novas      lê só o que este consumidor ainda não leu e grava a posição ao terminar
               (group.id próprio; na primeira vez lê tudo, depois só o que chegou desde a última)
  --ao-vivo    lê só o que for publicado com o consumidor ligado (ignora o que já está no tópico)

Uso:
    python scripts/consumidor_teste.py
    python scripts/consumidor_teste.py --max 10 --espera 15
    python scripts/consumidor_teste.py --novas
    python scripts/consumidor_teste.py --ao-vivo

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

GROUP_ID = "I7-verificacao"               # só deste script; não grava posição (relê tudo)
GROUP_ID_NOVAS = "I7-verificacao-novas"   # só deste script; grava a posição lida (--novas)
# O app (A8) usa o group.id dele (A8-acesso); estes dois não interferem nele.

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
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--novas", action="store_true",
                      help="lê só as mensagens que este consumidor ainda não leu e grava a posição "
                           "ao terminar (group.id próprio, diferente do app)")
    modo.add_argument("--ao-vivo", action="store_true", dest="ao_vivo",
                      help="lê só as mensagens publicadas depois que o script iniciou "
                           "(ignora o que já está no tópico)")
    args = parser.parse_args()

    try:
        from confluent_kafka import Consumer
    except ImportError:
        raise SystemExit("[ERRO] Instale a biblioteca: pip install confluent-kafka")

    topico = pe.topico()
    group_id = GROUP_ID_NOVAS if args.novas else GROUP_ID
    nome_modo = ("ainda não lidas (grava a posição ao terminar)" if args.novas
                 else "só as publicadas com o consumidor ligado" if args.ao_vivo
                 else "desde o início (não grava posição)")
    consumidor = Consumer({
        "bootstrap.servers": pe.broker(),
        "group.id": group_id,
        # sem posição salva: "earliest" começa da mensagem mais antiga; "latest" (--ao-vivo) só das novas.
        # Com --novas, a primeira execução lê tudo e as seguintes continuam de onde parou.
        "auto.offset.reset": "latest" if args.ao_vivo else "earliest",
        "enable.auto.commit": False,       # a posição só é gravada de propósito, no fim (--novas)
    })
    consumidor.subscribe([topico])
    print(f"Lendo '{topico}' em {pe.broker()} (group.id={group_id}). "
          f"Modo: {nome_modo}. "
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
        if args.novas and lidas:
            try:
                consumidor.commit(asynchronous=False)   # grava a posição: a próxima execução segue daqui
                print("\nPosição de leitura gravada (a próxima execução com --novas continua daqui).")
            except Exception as erro:
                logging.warning("não foi possível gravar a posição: %s", erro)
    finally:
        consumidor.close()

    print(f"\n{lidas} mensagem(ns) lida(s): {ok_guia} passam na validação do guia, "
          f"{ok_i7} no contrato completo da I7, {descartadas} descartada(s).")


if __name__ == "__main__":
    main()