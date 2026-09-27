# tuwayki-core

**Código de plataforma compartido para los productos TUWAYKIAPP.** Paquete Python que centraliza el
aislamiento **multi-tenant**, el RBAC base y utilidades comunes (auth, criptografía, cálculos fiscales,
formateo de moneda, exportaciones, timezone, etc.) reutilizadas por los tres sistemas de la marca:

- **SHOP** — [Sistema-de-Ventas](https://github.com/TreborOscorima/Sistema-de-Ventas) (retail / POS)
- **FOOD** — [Sistema-para-Food](https://github.com/TreborOscorima/Sistema-para-Food) (gastronomía)
- **LIFE** — Sistema-Gestion-Clinica (clínicas y centros estéticos)

> **Agnóstico de framework:** `tuwayki-core` **no importa Reflex** (ni Flask, ni FastAPI). Solo depende de
> SQLModel/SQLAlchemy y utilidades de plataforma. Por eso un upgrade de Reflex en un producto **no** obliga
> a tocar el core, y el core puede evolucionar sin arrastrar dependencias de UI.

- **Versión:** 1.0.0 · **Python:** ≥ 3.11 · **Repo:** público

---

## Qué contiene

| Módulo | Contenido |
|---|---|
| `tuwayki_core.enums` | Enums de dominio: `SaleStatus`, `PaymentMethodType`, `ReturnReason`, `ReceiptType`, `FiscalStatus`, … |
| `tuwayki_core.constants` | Constantes de plataforma |
| `tuwayki_core.countries` | Presets por país (moneda, impuestos, formato) |
| `tuwayki_core.utils.tenant` | **Aislamiento multi-tenant**: filtrado por `company_id` + `branch_id` vía `with_loader_criteria` sobre el evento `do_orm_execute` |
| `tuwayki_core.utils.auth` | Hashing de contraseñas/PINs (bcrypt), JWT |
| `tuwayki_core.utils.crypto` | Cifrado de secretos, metadatos de certificados |
| `tuwayki_core.utils.payment` | Lógica de medios de pago |
| `tuwayki_core.utils.calculations` | Cálculos monetarios con `Decimal` |
| `tuwayki_core.utils.fiscal_validators` · `tax_presets` | Validadores e impuestos (IGV, presets fiscales) |
| `tuwayki_core.utils.formatting` | Formateo de moneda/números (`currency_spec`, `format_number`, …) |
| `tuwayki_core.utils.exports` | Exportaciones a Excel (openpyxl) y PDF (reportlab) |
| `tuwayki_core.utils.dates` · `timezone` | Fechas y zonas horarias |
| `tuwayki_core.utils.sanitization` · `validators` | Saneamiento y validación de entrada |
| `tuwayki_core.utils.rate_limit` | Rate limiting (Redis) |
| `tuwayki_core.utils.db` · `env` · `logger` · `performance` | Infra: conexión DB, entorno, logging, métricas |
| `tuwayki_core.fiscal` | **Facturación electrónica** compartida (ver abajo) |

### Facturación electrónica (`tuwayki_core.fiscal`)

Conectores por país **sin base de datos ni credenciales guardadas**: cada sistema arma un
`Document` (precios con impuesto incluido), llama al conector con las credenciales YA descifradas
de la empresa y guarda el `IssueResult` en su propia tabla.

| Módulo | Qué hace |
|---|---|
| `fiscal.models` | `Document`, `Line`, `Buyer`, `Reference`, `IssueResult`; estados `autorizado` / `pendiente` / `rechazado` / `error` (`retryable` = reintentar el mismo número) |
| `fiscal.amounts` | Base + impuesto por línea; la cabecera siempre es la suma del detalle. La tasa sale de la empresa (Perú: 18 %, MYPE restaurantes 10,5 % en 2026) |
| `fiscal.peru_nubefact` | Perú vía Nubefact (manual JSON v3.0): emitir, consultar, anular. Valida antes de llamar (RUC, DNI, serie F/B, boleta ≥ S/ 700 con documento) y reintenta seguro con `codigo_unico` (código 23 → consulta). `verify_credentials`: prueba la ruta y el token de una empresa sin emitir |
| `fiscal.argentina` | Argentina vía ARCA (WSAA + WSFEv1): factura A/B/C, `CondicionIVAReceptorId` (RG 5616), Factura C con el subtotal en `ImpNeto`; tras un corte consulta el número (FECompConsultar) antes de darlo por fallido |
| `fiscal.argentina_certificate` | Certificado de ARCA sin OpenSSL: genera la clave RSA y la solicitud (CSR) con el formato de ARCA (`alias` = nombre del sistema) y avisa si el certificado no corresponde a la clave o a otro CUIT |

Pendiente de probar contra los entornos reales: requiere una cuenta de Nubefact (RUC de la
empresa) y un certificado de homologación de ARCA (CUIT). Tests: `pytest tests/`.

### Dependencias

`sqlmodel`, `sqlalchemy>=2.0`, `aiomysql`, `PyMySQL`, `cryptography`, `defusedxml`, `PyJWT`,
`httpx`, `python-dotenv`, `openpyxl`, `reportlab`, `redis`. (Sin dependencias de framework web.)

---

## Instalación / consumo

Los tres productos instalan el core **pinneado por commit SHA vía `requirements.txt`** (fuente de verdad
única, sin `_vendor`):

```
tuwayki-core @ git+https://github.com/TreborOscorima/tuwayki-core.git@<SHA>
```

Como el repo es **público**, la instalación no requiere autenticación (los Dockerfiles solo necesitan
`git` en el builder para resolver `git+https`).

Uso típico en un producto:

```python
from tuwayki_core.enums import PaymentMethodType
from tuwayki_core.utils.formatting import format_number, currency_spec
from tuwayki_core.utils.tenant import _refresh_tenant_models
```

### Desarrollo local (editable)

Para trabajar contra el core sin redeploy, clonarlo al lado del producto e instalarlo editable **después**
de las dependencias:

```bash
pip install -r requirements.txt
pip install -e ../tuwayki-core
```

---

## Bumpear el core (regla de oro)

El core es compartido: **no se modifica dentro del upgrade de otro sistema**. El flujo es:

1. Cambiar el core en **este** repo canónico y publicar el commit.
2. Re-pinnear el **mismo SHA** en el `requirements.txt` de los 3 productos, de forma **coordinada**.
3. Correr los tests de cada producto y redeployar.

El SHA es la fuente de verdad única en cada producto — mantener los 3 alineados.

---

## Instalar y testear el paquete solo

```bash
pip install -e .
python -c "import tuwayki_core; print(tuwayki_core.__version__)"
```

La API pública que los productos verifican en CI incluye: `tuwayki_core.enums`, `tuwayki_core.countries`,
`tuwayki_core.utils.tenant`, `tuwayki_core.utils.crypto`, `tuwayki_core.utils.calculations`,
`tuwayki_core.utils.formatting` y `tuwayki_core.utils.tax_presets`.
