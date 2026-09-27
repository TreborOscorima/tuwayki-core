"""Facturación electrónica compartida por todos los sistemas de TUWAYKIAPP.

Conectores por país SIN base de datos ni credenciales guardadas: cada sistema
arma un ``Document``, llama al conector con las credenciales (ya descifradas)
de la empresa y guarda el ``IssueResult`` en su propia tabla.

- ``models``: Document, Line, Buyer, IssueResult, estados.
- ``amounts``: base + impuesto desde precios con impuesto incluido.
- ``peru_nubefact``: Perú (SUNAT) vía Nubefact, y la verificación de la ruta y
  el token de una empresa.
- ``argentina``: Argentina (ARCA) vía WSAA + WSFEv1.
- ``argentina_certificate``: clave privada y solicitud (CSR) del certificado
  de ARCA, y control del par certificado + clave.
"""
from tuwayki_core.fiscal.amounts import compute_totals, split_tax
from tuwayki_core.fiscal.models import (
    Buyer,
    Document,
    DocumentType,
    FiscalStatus,
    IssueResult,
    Line,
    LineAmounts,
    Reference,
    TaxCategory,
    Totals,
)

__all__ = [
    "Buyer", "Document", "DocumentType", "FiscalStatus", "IssueResult", "Line",
    "LineAmounts", "Reference", "TaxCategory", "Totals", "compute_totals", "split_tax",
]
