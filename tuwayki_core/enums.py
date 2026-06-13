"""Shared enumerations for all TUWAYKIAPP products."""
from enum import Enum


class SaleStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    cancelled = "cancelled"
    returned = "returned"

    PENDING = pending
    COMPLETED = completed
    CANCELLED = cancelled
    RETURNED = returned


class PaymentMethodType(str, Enum):
    cash = "cash"
    debit = "debit"
    credit = "credit"
    yape = "yape"
    plin = "plin"
    transfer = "transfer"
    mixed = "mixed"
    other = "other"
    card = "card"
    wallet = "wallet"

    CASH = cash
    DEBIT = debit
    CREDIT = credit
    YAPE = yape
    PLIN = plin
    TRANSFER = transfer
    MIXED = mixed
    OTHER = other
    CARD = card
    WALLET = wallet


class ReturnReason(str, Enum):
    defective = "defective"
    wrong_item = "wrong_item"
    change_mind = "change_mind"
    not_as_described = "not_as_described"
    other = "other"

    @property
    def display_label(self) -> str:
        labels = {
            "defective": "Producto defectuoso",
            "wrong_item": "Producto equivocado",
            "change_mind": "Cambio de opinión",
            "not_as_described": "No es lo esperado",
            "other": "Otro motivo",
        }
        return labels.get(self.value, self.value)


class ReceiptType(str, Enum):
    nota_venta = "nota_venta"
    boleta = "boleta"
    factura = "factura"
    nota_credito = "nota_credito"
    nota_debito = "nota_debito"

    NOTA_VENTA = nota_venta
    BOLETA = boleta
    FACTURA = factura
    NOTA_CREDITO = nota_credito
    NOTA_DEBITO = nota_debito


class FiscalStatus(str, Enum):
    none = "none"
    pending = "pending"
    sent = "sent"
    authorized = "authorized"
    rejected = "rejected"
    error = "error"

    NONE = none
    PENDING = pending
    SENT = sent
    AUTHORIZED = authorized
    REJECTED = rejected
    ERROR = error
