"""Regras de negócio do fluxo demonstrativo de delivery."""

from __future__ import annotations

import json
import re
from typing import Any


CATALOG = {
    "PIZZA_MARGUERITA": ("Pizza Marguerita", 3890),
    "HAMBURGUER": ("Hambúrguer artesanal", 2990),
    "SUCO_LARANJA": ("Suco de laranja 300 ml", 890),
}
STATUS_SEQUENCE = ("EM_PREPARO", "SAIU_PARA_ENTREGA", "ENTREGA_SIMULADA")
ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,40}$")


def _invalid(reason: str, order_id: str | None = None) -> dict[str, Any]:
    return {"valid": False, "reason": reason, "order_id": order_id}


def _money(cents: int) -> str:
    return f"R$ {cents // 100},{cents % 100:02d}"


def validate_order(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Valida dados e calcula o total com preços do catálogo, sem confiar no cliente."""
    if not isinstance(event, dict):
        return _invalid("O pedido deve ser um objeto JSON.")

    order_id = event.get("pedido_id")
    if not isinstance(order_id, str) or not ORDER_ID_PATTERN.fullmatch(order_id):
        return _invalid("pedido_id inválido.")

    customer = event.get("cliente")
    first_name = customer.get("primeiro_nome") if isinstance(customer, dict) else None
    if (
        not isinstance(first_name, str)
        or not 1 <= len(first_name.strip()) <= 40
        or not all(c.isalpha() or c in " -'" for c in first_name.strip())
    ):
        return _invalid("Informe um primeiro nome válido.", order_id)
    first_name = first_name.strip()

    address = event.get("endereco")
    if not isinstance(address, dict) or any(
        not isinstance(address.get(key), str)
        or not 1 <= len(address[key].strip()) <= 100
        for key in ("logradouro", "numero", "cidade")
    ):
        return _invalid("Informe logradouro, número e cidade.", order_id)

    items = event.get("itens")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        return _invalid("Informe de 1 a 10 itens.", order_id)

    normalized_items = []
    total_cents = 0
    for item in items:
        if not isinstance(item, dict):
            return _invalid("Cada item deve ser um objeto.", order_id)
        sku, quantity = item.get("sku"), item.get("quantidade")
        if not isinstance(sku, str) or sku not in CATALOG or type(quantity) is not int or not 1 <= quantity <= 10:
            return _invalid("SKU ou quantidade inválidos.", order_id)
        name, unit_cents = CATALOG[sku]
        normalized_items.append({"sku": sku, "name": name, "quantity": quantity})
        total_cents += unit_cents * quantity

    payment = event.get("pagamento")
    payment_result = payment.get("resultado_simulado") if isinstance(payment, dict) else None
    if payment_result not in ("APROVADO", "RECUSADO"):
        return _invalid("Use pagamento.resultado_simulado APROVADO ou RECUSADO.", order_id)

    return {
        "valid": True,
        "order": {
            "order_id": order_id,
            "first_name": first_name,
            "items": normalized_items,
            "items_summary": ", ".join(
                f"{item['quantity']}x {item['name']}" for item in normalized_items
            ),
            "total_cents": total_cents,
            "total_brl": _money(total_cents),
            "payment_result": payment_result,
        },
    }


def process_payment(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Simula uma resposta de pagamento; não conversa com operadora financeira."""
    order = event["order"]
    return {
        "approved": order["payment_result"] == "APROVADO",
        "mode": "SIMULADO",
    }


def update_status(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Registra uma transição no próprio payload da execução."""
    history, new_status = event["history"], event["new_status"]
    if not isinstance(history, list) or not isinstance(new_status, str):
        raise ValueError("Histórico ou status inválido.")
    if len(history) >= len(STATUS_SEQUENCE) or new_status != STATUS_SEQUENCE[len(history)]:
        raise ValueError("Transição de status fora de ordem.")
    if history != list(STATUS_SEQUENCE[: len(history)]):
        raise ValueError("Histórico de status inconsistente.")
    return {
        "order_id": event["order_id"],
        "status": new_status,
        "history": [*history, new_status],
    }


def _bedrock_text(result: Any) -> str | None:
    """Aceita Body como JSON ou string JSON, conforme o retorno da integração."""
    try:
        body = result["Body"]
        if isinstance(body, str):
            body = json.loads(body)
        blocks = body["output"]["message"]["content"]
        text = next(
            block["text"]
            for block in blocks
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
        text = " ".join(text.split()).strip()[:160]
        return text or None
    except (KeyError, TypeError, ValueError, StopIteration):
        return None


def finalize_order(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Monta resultado estável, sem permitir que o modelo decida status ou total."""
    validation = event["validation_task"]["Payload"]
    if not validation["valid"]:
        return {
            "pedido_id": validation.get("order_id"),
            "status": "PEDIDO_INVALIDO",
            "motivo": validation["reason"],
        }

    order = validation["order"]
    payment = event["payment_task"]["Payload"]
    if not payment["approved"]:
        return {
            "pedido_id": order["order_id"],
            "status": "PAGAMENTO_RECUSADO_SIMULADO",
            "total": order["total_brl"],
            "mensagem": "Pagamento recusado na simulação. Nenhuma cobrança foi feita.",
        }

    delivery = event["status_delivered"]["Payload"]
    if delivery["status"] != "ENTREGA_SIMULADA":
        raise ValueError("Entrega simulada não foi concluída.")

    generated = _bedrock_text(event.get("bedrock"))
    fallback = f"Obrigado, {order['first_name']}! Esta simulação do pedido foi concluída."
    return {
        "pedido_id": order["order_id"],
        "status": delivery["status"],
        "historico": delivery["history"],
        "pagamento": "APROVADO_SIMULADO",
        "total": order["total_brl"],
        "mensagem": generated or fallback,
        "origem_mensagem": "bedrock" if generated else "reserva",
    }
