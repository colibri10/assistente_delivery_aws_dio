"""Simulador de regras de negócio; não emula serviços AWS."""

import json
import sys
from pathlib import Path

from src.handler import finalize_order, process_payment, update_status, validate_order


def simulate(order):
    event = {"validation_task": {"Payload": validate_order(order)}}
    validation = event["validation_task"]["Payload"]
    if not validation["valid"]:
        return finalize_order(event)

    event["payment_task"] = {"Payload": process_payment(validation)}
    if not event["payment_task"]["Payload"]["approved"]:
        return finalize_order(event)

    history = []
    for status in ("EM_PREPARO", "SAIU_PARA_ENTREGA", "ENTREGA_SIMULADA"):
        update = update_status(
            {"order_id": validation["order"]["order_id"], "new_status": status, "history": history}
        )
        history = update["history"]
    event["status_delivered"] = {"Payload": update}
    return finalize_order(event)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python demo_local.py examples/order-approved.json")
    order = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(simulate(order), ensure_ascii=False, indent=2))
