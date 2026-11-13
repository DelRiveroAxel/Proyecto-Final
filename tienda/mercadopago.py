"""
Cobro con QR de Mercado Pago usando la API de Orders (QR dinámico).

Documentación: https://www.mercadopago.com.ar/developers/es/docs/qr-code
  - Crear:     POST /v1/orders   (type "qr", config.qr.mode "dynamic")
  - Consultar: GET  /v1/orders/{id}
  - Cancelar:  POST /v1/orders/{id}/cancel
  - El QR viene en type_response.qr_data (texto EMVCo que hay que dibujar como QR).
  - Estado pagado: status "processed" (status_detail "accredited").
  - Las notificaciones se configuran en "Tus integraciones" > Webhooks, tópico "Order".

Requisitos para cobrar de verdad (ver README):
  1. Crear una aplicación en Mercado Pago Developers y copiar el Access Token.
  2. Crear una sucursal y una caja; su external_pos_id va en MP_EXTERNAL_POS_ID.
  3. Cargar MP_ACCESS_TOKEN en el .env. Sin token, todo funciona en MODO DEMO.
"""

import hashlib
import hmac
import logging
import uuid
from decimal import Decimal

import requests
from django.conf import settings

log = logging.getLogger(__name__)
API = 'https://api.mercadopago.com'


class ErrorMercadoPago(Exception):
    pass


def modo_demo():
    return settings.MP_MODO_DEMO


def _headers(idempotente=False):
    h = {'Authorization': f'Bearer {settings.MP_ACCESS_TOKEN}', 'Content-Type': 'application/json'}
    if idempotente:
        h['X-Idempotency-Key'] = str(uuid.uuid4())
    return h


def _monto(valor):
    return f'{Decimal(valor):.2f}'


def crear_orden_qr(pedido, minutos):
    """Crea la orden y devuelve (order_id, qr_data)."""
    if modo_demo():
        return f'DEMO-{pedido.pk}-{uuid.uuid4().hex[:8]}', f'IURKO|DEMO|{pedido.external_reference}|{_monto(pedido.total)}'

    items = [{
        'title': f'{it.nombre} ({it.cantidad} u. / {it.peso_kg:.3f} kg)'[:150],
        'unit_price': _monto(it.subtotal),
        'quantity': 1,
        'unit_measure': 'unit',
        'external_code': f'RECETA-{it.receta_id}',
    } for it in pedido.items.all()]
    body = {
        'type': 'qr',
        'external_reference': pedido.external_reference,
        'description': f'Iurko Chacinados - Pedido #{pedido.numero}',
        'expiration_time': f'PT{minutos}M',
        'total_amount': _monto(pedido.total),
        'config': {'qr': {'external_pos_id': settings.MP_EXTERNAL_POS_ID, 'mode': 'dynamic'}},
        'items': items,
    }
    try:
        r = requests.post(f'{API}/v1/orders', json=body, headers=_headers(True), timeout=15)
    except requests.RequestException as e:
        raise ErrorMercadoPago('No se pudo conectar con Mercado Pago.') from e
    if r.status_code >= 300:
        log.error('MP crear orden %s: %s', r.status_code, r.text)
        raise ErrorMercadoPago(f'Mercado Pago rechazó la orden ({r.status_code}).')
    data = r.json()
    return data['id'], data['type_response']['qr_data']


def obtener_orden(order_id):
    """Devuelve la orden completa de Mercado Pago (dict) o None."""
    if modo_demo() or not order_id:
        return None
    try:
        r = requests.get(f'{API}/v1/orders/{order_id}', headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        log.exception('MP consultar orden %s', order_id)
        return None


def consultar_orden(order_id):
    """Status de la orden: created / processed / canceled / refunded / expired."""
    orden = obtener_orden(order_id)
    return orden.get('status') if orden else None


def datos_del_pago(orden):
    """(n.º de operación, medio de pago) a partir de la orden de Mercado Pago."""
    if not orden:
        return '', ''
    pagos = ((orden.get('transactions') or {}).get('payments')) or []
    pago = next((p for p in pagos if p.get('status') in ('processed', 'accredited')), pagos[0] if pagos else {})
    metodo = (pago.get('payment_method') or {})
    return str(pago.get('reference_id') or pago.get('id') or ''), metodo.get('type') or metodo.get('id') or ''


def datos_demo(pedido):
    return {'id': pedido.mp_order_id, 'status': 'processed', 'modo': 'demo',
            'transactions': {'payments': [{'id': f'DEMO-PAY-{pedido.pk:06d}', 'status': 'processed',
                                           'amount': _monto(pedido.total),
                                           'payment_method': {'id': 'demo', 'type': 'demo'}}]}}


def cancelar_orden(order_id):
    if modo_demo() or not order_id:
        return
    try:
        requests.post(f'{API}/v1/orders/{order_id}/cancel', headers=_headers(True), timeout=10)
    except requests.RequestException:
        log.exception('MP cancelar orden %s', order_id)


def firma_valida(request, data_id):
    """Valida el header x-signature de los webhooks (si hay clave secreta configurada)."""
    secreto = settings.MP_WEBHOOK_SECRET
    if not secreto:
        return True
    firma = request.headers.get('x-signature', '')
    partes = dict(p.strip().split('=', 1) for p in firma.split(',') if '=' in p)
    ts, v1 = partes.get('ts'), partes.get('v1')
    if not ts or not v1:
        return False
    manifiesto = f'id:{str(data_id).lower()};request-id:{request.headers.get("x-request-id", "")};ts:{ts};'
    esperado = hmac.new(secreto.encode(), manifiesto.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, v1)
