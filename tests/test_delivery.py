"""Testa os três caminhos do pedido e invariantes de negócio relevantes."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo_local import simulate  # noqa: E402
from src.handler import finalize_order, update_status, validate_order  # noqa: E402


def example(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class DeliveryFlowTests(unittest.TestCase):
    def test_approved_order_reaches_simulated_delivery_with_catalog_total(self):
        order = example("order-approved.json")
        order["total_centavos"] = 1  # Campo externo não pode alterar o preço.
        result = simulate(order)
        self.assertEqual(result["status"], "ENTREGA_SIMULADA")
        self.assertEqual(result["total"], "R$ 56,70")
        self.assertEqual(result["origem_mensagem"], "reserva")
        self.assertEqual(result["historico"], ["EM_PREPARO", "SAIU_PARA_ENTREGA", "ENTREGA_SIMULADA"])

    def test_invalid_order_stops_before_payment_and_bedrock(self):
        result = simulate(example("order-invalid.json"))
        self.assertEqual(result["status"], "PEDIDO_INVALIDO")
        self.assertNotIn("pagamento", result)
        self.assertNotIn("origem_mensagem", result)

    def test_malformed_sku_is_rejected_without_crashing(self):
        order = example("order-approved.json")
        order["itens"][0]["sku"] = ["PIZZA_MARGUERITA"]
        self.assertEqual(simulate(order)["status"], "PEDIDO_INVALIDO")

    def test_declined_payment_stops_before_delivery_and_bedrock(self):
        result = simulate(example("order-payment-rejected.json"))
        self.assertEqual(result["status"], "PAGAMENTO_RECUSADO_SIMULADO")
        self.assertNotIn("historico", result)

    def test_status_cannot_skip_delivery_stages(self):
        with self.assertRaises(ValueError):
            update_status({"order_id": "PED-1", "new_status": "ENTREGA_SIMULADA", "history": []})

    def test_bedrock_text_does_not_override_authoritative_fields(self):
        validation = validate_order(example("order-approved.json"))
        event = {
            "validation_task": {"Payload": validation},
            "payment_task": {"Payload": {"approved": True}},
            "status_delivered": {"Payload": {"status": "ENTREGA_SIMULADA", "history": list(("EM_PREPARO", "SAIU_PARA_ENTREGA", "ENTREGA_SIMULADA"))}},
            "bedrock": {"Body": {"output": {"message": {"content": [{"text": "Obrigado, Ana!"}]}}}},
        }
        result = finalize_order(event)
        self.assertEqual(result["origem_mensagem"], "bedrock")
        self.assertEqual(result["mensagem"], "Obrigado, Ana!")
        self.assertEqual(result["status"], "ENTREGA_SIMULADA")
        self.assertEqual(result["total"], "R$ 56,70")

    def test_asl_destinations_exist_and_bedrock_has_fallback(self):
        workflow = json.loads((ROOT / "statemachine" / "delivery.asl.json").read_text(encoding="utf-8"))
        states = workflow["States"]
        self.assertIn(workflow["StartAt"], states)
        for state in states.values():
            destinations = [state.get("Next"), state.get("Default")]
            destinations += [choice["Next"] for choice in state.get("Choices", [])]
            destinations += [handler["Next"] for handler in state.get("Catch", [])]
            for destination in filter(None, destinations):
                self.assertIn(destination, states)
        self.assertEqual(states["PersonalizarMensagem"]["Catch"][0]["Next"], "GerarResposta")


if __name__ == "__main__":
    unittest.main()
