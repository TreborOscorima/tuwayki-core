"""Modelos neutros de un comprobante electrónico (sin base de datos).

Cada sistema (TUWAYKISHOP, TUWAYKIFOOD, TUWAYKILIFE) arma un ``Document``
con sus datos y lo emite con el conector del país. El conector devuelve un
``IssueResult``; el sistema guarda ese resultado en SU propia tabla.

Los precios de las líneas van CON impuesto incluido (como se cobra en caja);
``amounts.compute_totals`` separa base e impuesto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any


class DocumentType(str, Enum):
    INVOICE = "factura"
    RECEIPT = "boleta"            # Perú; en Argentina se emite como factura B/C
    CREDIT_NOTE = "nota_credito"
    DEBIT_NOTE = "nota_debito"


class TaxCategory(str, Enum):
    TAXED = "gravado"
    EXEMPT = "exonerado"
    UNAFFECTED = "inafecto"


class FiscalStatus(str, Enum):
    AUTHORIZED = "autorizado"   # aceptado por la autoridad (SUNAT / ARCA)
    PENDING = "pendiente"       # recibido, falta la respuesta: consultar después
    REJECTED = "rechazado"      # datos inválidos o rechazo fiscal: corregir
    ERROR = "error"             # no se pudo completar (red, servidor, cuenta): reintentar


@dataclass(frozen=True)
class Buyer:
    """Cliente del comprobante.

    ``doc_type`` usa el código del país:
      - Perú (Nubefact): "6" RUC, "1" DNI, "4" carné de extranjería,
        "7" pasaporte, "-" varios (sin documento).
      - Argentina (ARCA): "80" CUIT, "96" DNI, "99" consumidor final.
    """
    doc_type: str
    doc_number: str = ""
    name: str = ""
    address: str = ""
    email: str = ""
    # Argentina: condición frente al IVA ("RI", "monotributo", "exento", "CF").
    vat_condition: str = "CF"


@dataclass(frozen=True)
class Line:
    description: str
    quantity: Decimal
    unit_price: Decimal                  # CON impuesto incluido
    code: str = ""
    unit: str = "NIU"                    # NIU = producto, ZZ = servicio
    category: TaxCategory = TaxCategory.TAXED


@dataclass(frozen=True)
class Reference:
    """Comprobante que modifica una nota de crédito/débito."""
    doc_type: DocumentType
    series: str
    number: int
    reason_code: int                     # Perú: tipo_de_nota_de_credito/debito
    reason: str = ""
    # Argentina (CbtesAsoc de ARCA): letra, punto de venta y fecha del
    # comprobante original. La nota lleva la letra del original; sin punto de
    # venta se usa el del emisor. Perú no los usa.
    letter: str = ""
    point_of_sale: int | None = None
    issue_date: date | None = None


@dataclass
class Document:
    country: str                         # "PE", "AR"
    doc_type: DocumentType
    series: str                          # Perú: "B001"/"F001"; Argentina: punto de venta ("1")
    number: int
    issue_date: date
    currency: str                        # "PEN", "USD", "ARS"
    tax_rate: Decimal                    # en %: 18, 10.5, 21 (de la config de la empresa)
    lines: list[Line]
    buyer: Buyer | None = None
    global_discount: Decimal = Decimal("0")   # CON impuesto, sobre lo gravado
    notes: str = ""
    reference: Reference | None = None
    exchange_rate: Decimal | None = None


@dataclass(frozen=True)
class LineAmounts:
    unit_value: Decimal                  # sin impuesto, hasta 10 decimales
    base: Decimal                        # sin impuesto, 2 decimales
    tax: Decimal
    total: Decimal                       # con impuesto


@dataclass(frozen=True)
class Totals:
    lines: list[LineAmounts]
    taxed: Decimal                       # base gravada (ya con el descuento)
    exempt: Decimal
    unaffected: Decimal
    tax: Decimal
    discount_base: Decimal               # descuento global sin impuesto
    discount_total: Decimal              # descuento global con impuesto
    total: Decimal


@dataclass
class IssueResult:
    """Resultado de emitir, consultar o anular. Nunca incluye credenciales."""
    status: FiscalStatus
    message: str = ""
    error_code: str = ""
    authorization_code: str = ""         # Perú: hash; Argentina: CAE
    authorization_expiry: str = ""       # Argentina: vencimiento del CAE (AAAAMMDD)
    qr_data: str = ""
    pdf_url: str = ""
    xml_url: str = ""
    cdr_url: str = ""
    voided: bool = False
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] | str = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == FiscalStatus.AUTHORIZED

    @property
    def retryable(self) -> bool:
        """Se puede reintentar el MISMO comprobante (mismo número) más tarde."""
        return self.status in (FiscalStatus.PENDING, FiscalStatus.ERROR)
