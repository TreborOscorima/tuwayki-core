"""Argentina: WSAA + WSFEv1 con ARCA simulado (certificado de prueba real)."""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import re
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from tuwayki_core.fiscal import argentina as arca
from tuwayki_core.fiscal import argentina_wsaa as wsaa
from tuwayki_core.fiscal import argentina_wsfe as wsfe
from tuwayki_core.fiscal.amounts import compute_totals
from tuwayki_core.fiscal.models import Buyer, Document, DocumentType, FiscalStatus, Line

D = Decimal
CUIT = "20409378472"   # CUIT de prueba con dígito verificador válido


@pytest.fixture(scope="module")
def cert_y_clave():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tuwayki-test"),
                      x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {CUIT}")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()),
    )


@pytest.fixture(autouse=True)
def _cache_limpia():
    wsaa.clear_cache()
    yield
    wsaa.clear_cache()


def _issuer(cert_y_clave, vat="RI") -> arca.ArcaIssuer:
    cert, key = cert_y_clave
    return arca.ArcaIssuer(CUIT, 3, vat, cert, key, "sandbox")


def _doc(**kw) -> Document:
    base = dict(
        country="AR", doc_type=DocumentType.RECEIPT, series="3", number=42,
        issue_date=dt.date(2026, 9, 26), currency="ARS", tax_rate=D("21"),
        lines=[Line("Milanesa napolitana", D("2"), D("12100.00")),
               Line("Agua", D("1"), D("1815.00"))],
    )
    base.update(kw)
    return Document(**base)


def _login_xml(token="TOKEN123", sign="SIGN456") -> str:
    ticket = (
        '<?xml version="1.0" encoding="UTF-8"?><loginTicketResponse><header>'
        "<expirationTime>2099-01-01T00:00:00-03:00</expirationTime></header>"
        f"<credentials><token>{token}</token><sign>{sign}</sign></credentials>"
        "</loginTicketResponse>"
    )
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        f"<loginCmsResponse><loginCmsReturn>{escape(ticket)}</loginCmsReturn></loginCmsResponse>"
        "</soap:Body></soap:Envelope>"
    )


def _cae_xml(resultado="A", cae="76123456789012", nro=42, obs="") -> str:
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        '<FECAESolicitarResponse xmlns="http://ar.gov.afip.dif.FEV1/"><FECAESolicitarResult>'
        f"<FeCabResp><Resultado>{resultado}</Resultado></FeCabResp>"
        "<FeDetResp><FECAEDetResponse>"
        f"<CbteDesde>{nro}</CbteDesde><CbteHasta>{nro}</CbteHasta>"
        f"<Resultado>{resultado}</Resultado><CAE>{cae}</CAE><CAEFchVto>20261006</CAEFchVto>"
        f"{obs}"
        "</FECAEDetResponse></FeDetResp>"
        "</FECAESolicitarResult></FECAESolicitarResponse></soap:Body></soap:Envelope>"
    )


def _consulta_xml(cae="76999999999999") -> str:
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        '<FECompConsultarResponse xmlns="http://ar.gov.afip.dif.FEV1/"><FECompConsultarResult>'
        "<ResultGet><Resultado>A</Resultado>"
        f"<CodAutorizacion>{cae}</CodAutorizacion><FchVto>20261006</FchVto>"
        "<ImpTotal>26015.00</ImpTotal></ResultGet>"
        "</FECompConsultarResult></FECompConsultarResponse></soap:Body></soap:Envelope>"
    )


class _Arca:
    """ARCA falso: WSAA + WSFEv1 según el host y el SOAPAction."""

    def __init__(self, wsfe_responses: dict[str, list]):
        self.wsfe = {k: list(v) for k, v in wsfe_responses.items()}
        self.logins = 0
        self.bodies: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if "wsaa" in request.url.host:
            self.logins += 1
            cms = re.search(r"<wsaa:in0>(.*)</wsaa:in0>", request.content.decode()).group(1)
            pkcs7.load_der_pkcs7_certificates(base64.b64decode(cms))  # firma CMS válida
            return httpx.Response(200, text=_login_xml())
        action = request.headers["SOAPAction"].strip('"').rsplit("/", 1)[-1]
        self.bodies[action] = request.content.decode()
        item = self.wsfe[action].pop(0)
        if isinstance(item, Exception):
            raise item
        return httpx.Response(200, text=item)

    @property
    def transport(self):
        return httpx.MockTransport(self)


def _run(coro):
    return asyncio.run(coro)


# ── Letra, montos y XML ─────────────────────────────────────────────────

@pytest.mark.parametrize("emisor, receptor, letra", [
    ("RI", "RI", "A"), ("RI", "CF", "B"), ("RI", "monotributo", "B"),
    ("monotributo", "RI", "C"), ("exento", "CF", "C"),
])
def test_letra(emisor, receptor, letra):
    assert arca.invoice_letter(emisor, receptor) == letra


def test_factura_b_discrimina_iva_y_manda_condicion_del_receptor(cert_y_clave):
    doc = _doc()
    t = compute_totals(doc.lines, doc.tax_rate)
    req = arca.build_request(doc, _issuer(cert_y_clave), t)
    assert (req.cbte_tipo, req.tipo_doc, req.nro_doc) == (6, 99, 0)
    assert req.imp_neto + req.imp_iva == req.imp_total == D("26015.00")
    assert req.iva_items == [{"Id": 5, "BaseImp": t.taxed, "Importe": t.tax}]
    assert req.condicion_iva_receptor == 5


def test_alicuota_reducida_10_5(cert_y_clave):
    doc = _doc(tax_rate=D("10.5"))
    req = arca.build_request(doc, _issuer(cert_y_clave), compute_totals(doc.lines, doc.tax_rate))
    assert req.iva_items[0]["Id"] == 4


def test_factura_c_subtotal_en_imp_neto(cert_y_clave):
    doc = _doc()
    req = arca.build_request(doc, _issuer(cert_y_clave, "monotributo"),
                             compute_totals(doc.lines, doc.tax_rate))
    assert req.cbte_tipo == 11
    assert (req.imp_neto, req.imp_tot_conc, req.imp_iva, req.imp_op_ex) == (
        D("26015.00"), D("0"), D("0"), D("0"))
    assert req.iva_items == []


def test_factura_a_con_cuit_del_cliente(cert_y_clave):
    doc = _doc(doc_type=DocumentType.INVOICE, buyer=Buyer("80", "30712345671", "Cliente SA",
                                                          vat_condition="RI"))
    req = arca.build_request(doc, _issuer(cert_y_clave), compute_totals(doc.lines, doc.tax_rate))
    assert (req.cbte_tipo, req.tipo_doc, req.nro_doc, req.condicion_iva_receptor) == (
        1, 80, 30712345671, 1)


def test_orden_del_xml_segun_el_manual():
    req = wsfe.FECAERequest(cbte_tipo=6, punto_vta=3, concepto=2, cbte_desde=1, cbte_hasta=1,
                            fecha_cbte="20260926", imp_total=121, imp_neto=100, imp_iva=21,
                            fecha_serv_desde="20260926", fecha_serv_hasta="20260926",
                            fecha_vto_pago="20260926", condicion_iva_receptor=5,
                            iva_items=[{"Id": 5, "BaseImp": 100, "Importe": 21}])
    xml = wsfe.build_fecae_request_xml("T", "S", int(CUIT), req)
    orden = ["ImpIVA", "FchServDesde", "FchVtoPago", "MonId", "MonCotiz",
             "CondicionIVAReceptorId", "<wsfe:Iva>"]
    posiciones = [xml.index(tag) for tag in orden]
    assert posiciones == sorted(posiciones)
    assert "<wsfe:ImpTotal>121.00</wsfe:ImpTotal>" in xml


def test_xml_escapa_texto():
    req = wsfe.FECAERequest(cbte_tipo=6, punto_vta=1, cbte_desde=1, cbte_hasta=1, mon_id='P<&"')
    assert 'P&lt;&amp;&quot;' in wsfe.build_fecae_request_xml("T", "S", 1, req)


# ── Flujo completo ──────────────────────────────────────────────────────

def test_emite_y_devuelve_cae_y_qr(cert_y_clave):
    srv = _Arca({"FECAESolicitar": [_cae_xml()]})
    r = _run(arca.issue(_doc(), _issuer(cert_y_clave), transport=srv.transport))
    assert r.status == FiscalStatus.AUTHORIZED
    assert (r.authorization_code, r.authorization_expiry) == ("76123456789012", "20261006")
    qr = json.loads(base64.b64decode(parse_qs(urlparse(r.qr_data).query)["p"][0]))
    assert qr["cuit"] == int(CUIT) and qr["codAut"] == 76123456789012 and qr["nroCmp"] == 42
    assert "<wsfe:Token>TOKEN123</wsfe:Token>" in srv.bodies["FECAESolicitar"]
    assert "TOKEN123" not in json.dumps(r.request)


def test_el_token_de_wsaa_se_reutiliza(cert_y_clave):
    srv = _Arca({"FECAESolicitar": [_cae_xml(nro=42), _cae_xml(nro=43)]})
    _run(arca.issue(_doc(), _issuer(cert_y_clave), transport=srv.transport))
    _run(arca.issue(_doc(number=43), _issuer(cert_y_clave), transport=srv.transport))
    assert srv.logins == 1


def test_rechazo_por_condicion_iva(cert_y_clave):
    obs = ("<Observaciones><Obs><Code>10242</Code><Msg>El campo Condicion IVA receptor "
           "no es un valor valido</Msg></Obs></Observaciones>")
    srv = _Arca({"FECAESolicitar": [_cae_xml("R", "", obs=obs)]})
    r = _run(arca.issue(_doc(), _issuer(cert_y_clave), transport=srv.transport))
    assert r.status == FiscalStatus.REJECTED and "10242" in r.message


def test_corte_de_red_se_recupera_consultando(cert_y_clave):
    srv = _Arca({"FECAESolicitar": [httpx.ReadTimeout("sin respuesta")],
                 "FECompConsultar": [_consulta_xml()]})
    r = _run(arca.issue(_doc(), _issuer(cert_y_clave), transport=srv.transport))
    assert r.status == FiscalStatus.AUTHORIZED and r.authorization_code == "76999999999999"
    assert "<wsfe:CbteNro>42</wsfe:CbteNro>" in srv.bodies["FECompConsultar"]


def test_corte_de_red_sin_comprobante_queda_para_reintentar(cert_y_clave):
    vacio = ('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
             '<FECompConsultarResponse xmlns="http://ar.gov.afip.dif.FEV1/"><FECompConsultarResult>'
             "<Errors><Err><Code>602</Code><Msg>Sin Resultados</Msg></Err></Errors>"
             "</FECompConsultarResult></FECompConsultarResponse></soap:Body></soap:Envelope>")
    srv = _Arca({"FECAESolicitar": [httpx.ConnectError("caído")], "FECompConsultar": [vacio]})
    r = _run(arca.issue(_doc(), _issuer(cert_y_clave), transport=srv.transport))
    assert r.status == FiscalStatus.ERROR and r.retryable


def test_validaciones_antes_de_llamar(cert_y_clave):
    cert, key = cert_y_clave
    malo = arca.ArcaIssuer("20409378471", 0, "RI", cert, key, "prod")
    srv = _Arca({})
    r = _run(arca.issue(_doc(), malo, transport=srv.transport))
    assert r.status == FiscalStatus.REJECTED
    assert "CUIT" in r.message and "punto de venta" in r.message and "Ambiente" in r.message
    assert srv.logins == 0


def test_certificado_invalido(cert_y_clave):
    malo = arca.ArcaIssuer(CUIT, 3, "RI", b"no es un pem", b"tampoco", "sandbox")
    r = _run(arca.issue(_doc(), malo, transport=_Arca({}).transport))
    assert r.status == FiscalStatus.ERROR and "Certificado PEM inválido" in r.message


def test_login_wsaa_parseo_y_errores():
    creds = wsaa.parse_login_response(_login_xml("A", "B"))
    assert (creds.token, creds.sign) == ("A", "B") and creds.is_valid
    with pytest.raises(ValueError, match="loginCmsReturn"):
        wsaa.parse_login_response(
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soap:Body><soap:Fault><faultstring>cms.cert.untrusted</faultstring></soap:Fault>"
            "</soap:Body></soap:Envelope>")
