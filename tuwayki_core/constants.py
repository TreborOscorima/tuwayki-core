"""Platform-level constants shared across all TUWAYKIAPP products."""
from __future__ import annotations

import os

# =============================================================================
# CONTACTO / WHATSAPP
# =============================================================================

WHATSAPP_NUMBER: str = "5491168376517"
WHATSAPP_SALES_URL: str = "https://wa.me/message/ULLEZ4HUFB5HA1"


# =============================================================================
# LÍMITES DE BÚSQUEDA Y PAGINACIÓN
# =============================================================================

CLIENT_SUGGESTIONS_LIMIT: int = 6
PRODUCT_SUGGESTIONS_LIMIT: int = 5
INVENTORY_RECENT_LIMIT: int = 100
DEFAULT_ITEMS_PER_PAGE: int = 10
CASHBOX_ITEMS_PER_PAGE: int = 5


# =============================================================================
# LÍMITES DE TEXTO
# =============================================================================

NOTES_MAX_LENGTH: int = 250
DESCRIPTION_MAX_LENGTH: int = 200
NAME_MAX_LENGTH: int = 100
ADDRESS_MAX_LENGTH: int = 300
REASON_MAX_LENGTH: int = 200


# =============================================================================
# CONFIGURACIÓN DE CRÉDITOS
# =============================================================================

DEFAULT_CREDIT_INTERVAL_DAYS: int = 30
DEFAULT_INSTALLMENTS_COUNT: int = 1
MIN_INSTALLMENTS: int = 1
MAX_INSTALLMENTS: int = 36


# =============================================================================
# CONFIGURACIÓN DE CONTRASEÑAS
# =============================================================================

PASSWORD_MIN_LENGTH: int = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))
LOGIN_LOCKOUT_MINUTES: int = 15
MAX_LOGIN_ATTEMPTS: int = 5


def _env_bool(var_name: str, default: bool = True) -> bool:
    val = os.getenv(var_name, "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}


PASSWORD_REQUIRE_UPPERCASE: bool = _env_bool("PASSWORD_REQUIRE_UPPERCASE", True)
PASSWORD_REQUIRE_DIGIT: bool = _env_bool("PASSWORD_REQUIRE_DIGIT", True)
PASSWORD_REQUIRE_SPECIAL: bool = _env_bool("PASSWORD_REQUIRE_SPECIAL", False)


# =============================================================================
# CONFIGURACIÓN DE RECIBOS
# =============================================================================

DEFAULT_RECEIPT_WIDTH: int = 42
MIN_RECEIPT_WIDTH: int = 24
MAX_RECEIPT_WIDTH: int = 64
DEFAULT_PAPER_WIDTH_MM: int = 80


# =============================================================================
# TOKENS Y SESIONES
# =============================================================================

TOKEN_EXPIRY_HOURS: int = 24
REFRESH_TOKEN_EXPIRY_DAYS: int = 7


# =============================================================================
# CONFIGURACIÓN DE DECIMALES
# =============================================================================

STOCK_DECIMAL_PLACES: int = 4
MONEY_DECIMAL_PLACES: int = 2


# =============================================================================
# ACCIONES DE CAJA
# =============================================================================

CASHBOX_INCOME_ACTIONS: set[str] = {
    "Venta",
    "Inicial Credito",
    "Reserva",
    "Adelanto",
    "Cobranza",
    "Cobro de Cuota",
    "Pago Cuota",
    "Cobro Cuota",
    "Ingreso Cuota",
    "Amortizacion",
    "Pago Credito",
    "ingreso_caja_chica",
}

CASHBOX_EXPENSE_ACTIONS: set[str] = {
    "gasto_caja_chica",
    "Devolucion",
}


# =============================================================================
# REPORTES
# =============================================================================

MAX_REPORT_ROWS: int = int(os.getenv("MAX_REPORT_ROWS", "25000"))
