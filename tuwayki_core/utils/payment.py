"""Payment method normalization and labeling utilities."""
from __future__ import annotations

from tuwayki_core.enums import PaymentMethodType


def normalize_payment_method_kind(kind: str) -> PaymentMethodType:
    normalized = (kind or "").strip().lower()
    mapping = {
        "cash": PaymentMethodType.cash, "efectivo": PaymentMethodType.cash,
        "debit": PaymentMethodType.debit, "debito": PaymentMethodType.debit,
        "credit": PaymentMethodType.credit, "credito": PaymentMethodType.credit,
        "yape": PaymentMethodType.yape, "plin": PaymentMethodType.plin,
        "transfer": PaymentMethodType.transfer, "transferencia": PaymentMethodType.transfer,
        "mixed": PaymentMethodType.mixed, "mixto": PaymentMethodType.mixed,
        "card": PaymentMethodType.credit, "tarjeta": PaymentMethodType.credit,
        "wallet": PaymentMethodType.yape, "billetera": PaymentMethodType.yape,
    }
    return mapping.get(normalized, PaymentMethodType.other)


def card_method_type(card_type: str) -> PaymentMethodType:
    value = (card_type or "").strip().lower()
    if "deb" in value or "debito" in value or "débito" in value:
        return PaymentMethodType.debit
    return PaymentMethodType.credit


def wallet_method_type(provider: str) -> PaymentMethodType:
    value = (provider or "").strip().lower()
    if "plin" in value:
        return PaymentMethodType.plin
    return PaymentMethodType.yape


def payment_method_code(method_type: PaymentMethodType) -> str | None:
    mapping = {
        PaymentMethodType.cash: "cash", PaymentMethodType.yape: "yape",
        PaymentMethodType.plin: "plin", PaymentMethodType.transfer: "transfer",
        PaymentMethodType.debit: "debit_card", PaymentMethodType.credit: "credit_card",
    }
    return mapping.get(method_type)


def payment_method_label(kind: str) -> str:
    normalized = (kind or "").strip().lower()
    labels = {
        "cash": "Efectivo", "debit": "Tarjeta de Débito", "credit": "Tarjeta de Crédito",
        "yape": "Billetera Digital (Yape)", "plin": "Billetera Digital (Plin)",
        "transfer": "Transferencia Bancaria", "mixed": "Pago Mixto",
        "card": "Tarjeta de Crédito", "wallet": "Billetera Digital (Yape)", "other": "Otros",
    }
    return labels.get(normalized, "Otros")


def normalize_wallet_label(label: str) -> str:
    value = (label or "").strip()
    if not value:
        return value
    key = value.lower()
    mapping = {
        "cash": "Efectivo", "debit": "Tarjeta de Débito", "credit": "Tarjeta de Crédito",
        "yape": "Billetera Digital (Yape)", "plin": "Billetera Digital (Plin)",
        "transfer": "Transferencia Bancaria", "mixed": "Pago Mixto", "other": "Otros",
    }
    if key in mapping:
        return mapping[key]
    if key == "card":
        return mapping["credit"]
    if key == "wallet":
        return mapping["yape"]
    if "mixto" in key and "(" in value and ")" in value:
        return f"{mapping['mixed']} {value[value.find('('):].strip()}"
    if "mixto" in key:
        return mapping["mixed"]
    if "debito" in key or "débito" in key:
        return mapping["debit"]
    if "credito" in key or "crédito" in key or "tarjeta" in key:
        return mapping["credit"]
    if "yape" in key:
        return mapping["yape"]
    if "plin" in key:
        return mapping["plin"]
    if "billetera" in key or "qr" in key:
        return mapping["yape"]
    if "transfer" in key or "banco" in key:
        return mapping["transfer"]
    if "efectivo" in key:
        return mapping["cash"]
    return value


def payment_category(method: str, kind: str = "") -> str:
    normalized_kind = (kind or "").lower()
    label = method.lower() if method else ""
    mapping = {
        "cash": "Efectivo", "debit": "Tarjeta de Débito", "credit": "Tarjeta de Crédito",
        "yape": "Billetera Digital (Yape)", "plin": "Billetera Digital (Plin)",
        "transfer": "Transferencia Bancaria", "mixed": "Pago Mixto", "other": "Otros",
    }
    if normalized_kind == "mixed" or "mixto" in label:
        return mapping["mixed"]
    if normalized_kind == "debit" or "debito" in label or "débito" in label:
        return mapping["debit"]
    if normalized_kind == "credit" or "credito" in label or "crédito" in label or "tarjeta" in label:
        return mapping["credit"]
    if normalized_kind == "yape" or "yape" in label:
        return mapping["yape"]
    if normalized_kind == "plin" or "plin" in label:
        return mapping["plin"]
    if normalized_kind == "transfer" or "transfer" in label or "banco" in label:
        return mapping["transfer"]
    if normalized_kind == "cash" or "efectivo" in label:
        return mapping["cash"]
    return mapping["other"]
