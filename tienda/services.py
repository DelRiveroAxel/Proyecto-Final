"""Lógica de la tienda: reservar piezas al generar el QR, confirmar pagos, avisos de stock."""

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from produccion.models import Pieza, Receta, Tanda
from produccion.services import actualizar_estado_agotada

from . import mercadopago as mp
from .models import AvisoStock, ItemCarrito, Pedido, PedidoItem, PiezaPedido

log = logging.getLogger(__name__)
MARGEN_RESERVA_MIN = 2   # la reserva dura 2 minutos más que el QR


class StockInsuficiente(ValidationError):
    def __init__(self, faltantes):
        self.faltantes = faltantes   # [(receta, pedidas, disponibles)]
        texto = '; '.join(f'{r.nombre}: pediste {p} y quedan {d}' for r, p, d in faltantes)
        super().__init__(f'No hay piezas suficientes. {texto}')


# ---------------------------------------------------------------------------
# Stock y carrito
# ---------------------------------------------------------------------------

def stock_por_receta(recetas=None):
    """{receta_id: piezas a la venta}. Libera antes las reservas vencidas."""
    liberar_reservas_vencidas()
    from django.db.models import Count
    qs = Pieza.objects.a_la_venta()
    if recetas is not None:
        qs = qs.filter(tanda__receta__in=recetas)
    return dict(qs.order_by().values_list('tanda__receta').annotate(n=Count('id')))


def resumen_carrito(user):
    """Renglones del carrito con stock y precio estimado (sin reservar nada)."""
    items = list(ItemCarrito.objects.filter(user=user).select_related('receta'))
    stock = stock_por_receta([i.receta for i in items])
    filas, total, hay_faltante = [], Decimal('0'), False
    for it in items:
        disp = stock.get(it.receta_id, 0)
        # Peso estimado con las piezas que hoy saldrían primero (FIFO)
        pesos = list(it.receta.piezas_a_la_venta().values_list('peso_g', flat=True)[:it.cantidad])
        peso = sum(pesos)
        sub = (it.receta.precio_kg * Decimal(peso) / 1000).quantize(Decimal('1'))
        falta = it.cantidad > disp
        hay_faltante |= falta
        total += sub
        filas.append({'item': it, 'receta': it.receta, 'disponibles': disp, 'falta': falta,
                      'peso_g': peso, 'subtotal': sub})
    return {'filas': filas, 'total': total, 'hay_faltante': hay_faltante,
            'cantidad': sum(i.cantidad for i in items)}


# ---------------------------------------------------------------------------
# Reserva al generar el QR
# ---------------------------------------------------------------------------

def _liberar_piezas(pedido):
    piezas = Pieza.objects.filter(venta__item__pedido=pedido, estado=Pieza.RESERVADA)
    tandas = set(piezas.values_list('tanda_id', flat=True))
    piezas.update(estado=Pieza.DISPONIBLE)
    PiezaPedido.objects.filter(item__pedido=pedido).delete()
    for t in Tanda.objects.filter(pk__in=tandas):
        actualizar_estado_agotada(t)


def liberar_reservas_vencidas():
    """Pedidos con QR vencido sin pagar: las piezas vuelven al stock."""
    vencidos = list(Pedido.objects.filter(estado=Pedido.PENDIENTE_PAGO, reserva_hasta__lt=timezone.now()))
    for p in vencidos:
        with transaction.atomic():
            p = Pedido.objects.select_for_update().get(pk=p.pk)
            if p.estado != Pedido.PENDIENTE_PAGO:
                continue
            _liberar_piezas(p)
            p.estado = Pedido.VENCIDO
            p.save(update_fields=['estado'])
    return len(vencidos)


@transaction.atomic
def _reservar(user, notas):
    # Si tenía otro QR pendiente, se cancela y se liberan esas piezas
    for anterior in Pedido.objects.select_for_update().filter(cliente=user, estado=Pedido.PENDIENTE_PAGO):
        _liberar_piezas(anterior)
        anterior.estado = Pedido.CANCELADO
        anterior.motivo_cancelacion = 'Reemplazado por un QR nuevo'
        anterior.save(update_fields=['estado', 'motivo_cancelacion'])
        transaction.on_commit(lambda oid=anterior.mp_order_id: mp.cancelar_orden(oid))

    carrito = list(ItemCarrito.objects.filter(user=user).select_related('receta'))
    if not carrito:
        raise ValidationError('El carrito está vacío.')

    minutos = settings.MP_QR_MINUTOS
    pedido = Pedido.objects.create(
        cliente=user, notas=notas,
        reserva_hasta=timezone.now() + timedelta(minutes=minutos + MARGEN_RESERVA_MIN),
    )
    faltantes, total = [], Decimal('0')
    for it in carrito:
        receta = it.receta
        if not receta.publicada:
            faltantes.append((receta, it.cantidad, 0))
            continue
        piezas = list(receta.piezas_a_la_venta().select_for_update()[:it.cantidad])
        if len(piezas) < it.cantidad:
            faltantes.append((receta, it.cantidad, len(piezas)))
            continue
        peso = sum(p.peso_g for p in piezas)
        sub = (receta.precio_kg * Decimal(peso) / 1000).quantize(Decimal('1'))
        item = PedidoItem.objects.create(pedido=pedido, receta=receta, nombre=receta.nombre,
                                         cantidad=it.cantidad, precio_kg=receta.precio_kg,
                                         peso_g=peso, subtotal=sub)
        # Marca RESERVADA solo si sigue DISPONIBLE (evita que dos clientes tomen la misma pieza)
        tomadas = Pieza.objects.filter(pk__in=[p.pk for p in piezas], estado=Pieza.DISPONIBLE).update(
            estado=Pieza.RESERVADA)
        if tomadas != len(piezas):
            raise StockInsuficiente([(receta, it.cantidad, tomadas)])
        PiezaPedido.objects.bulk_create([PiezaPedido(item=item, pieza=p) for p in piezas])
        total += sub
    if faltantes:
        raise StockInsuficiente(faltantes)     # rollback: no queda nada reservado

    pedido.total = total
    pedido.save(update_fields=['total'])
    return pedido


def crear_pedido_con_qr(user, notas=''):
    """Reserva las piezas FIFO por 15 minutos y genera el QR de Mercado Pago."""
    liberar_reservas_vencidas()
    pedido = _reservar(user, notas)
    try:
        order_id, qr = mp.crear_orden_qr(pedido, settings.MP_QR_MINUTOS)
    except mp.ErrorMercadoPago:
        cancelar_pedido_pendiente(pedido, 'No se pudo generar el QR')
        raise
    pedido.mp_order_id, pedido.qr_data = order_id, qr
    pedido.save(update_fields=['mp_order_id', 'qr_data'])
    return pedido


# ---------------------------------------------------------------------------
# Pago
# ---------------------------------------------------------------------------

def _guardar_datos_pago(pedido, orden):
    if orden is None and mp.modo_demo():
        orden = mp.datos_demo(pedido)
    operacion, metodo = mp.datos_del_pago(orden)
    pedido.mp_datos, pedido.mp_payment_id, pedido.mp_metodo = orden, operacion, metodo


@transaction.atomic
def confirmar_pago(pedido, orden=None):
    """Mercado Pago confirmó el pago: las piezas reservadas pasan a vendidas.
    `orden` es la respuesta de Mercado Pago (se guarda para el comprobante)."""
    pedido = Pedido.objects.select_for_update().get(pk=pedido.pk)
    if pedido.estado not in (Pedido.PENDIENTE_PAGO, Pedido.VENCIDO, Pedido.CANCELADO):
        return pedido                                       # ya estaba confirmado
    if pedido.estado == Pedido.CANCELADO and pedido.reintegro_pendiente:
        return pedido
    _guardar_datos_pago(pedido, orden)

    if pedido.estado != Pedido.PENDIENTE_PAGO:
        # Llegó el pago de un QR que ya habíamos liberado: se intenta reservar de nuevo
        for item in pedido.items.all():
            piezas = list(item.receta.piezas_a_la_venta().select_for_update()[:item.cantidad])
            if len(piezas) < item.cantidad:
                pedido.estado = Pedido.CANCELADO
                pedido.reintegro_pendiente = True
                pedido.pagado_en = timezone.now()
                pedido.motivo_cancelacion = 'Pago recibido sin stock: hay que reintegrar el dinero'
                pedido.novedad = True
                pedido.save()
                return pedido
            Pieza.objects.filter(pk__in=[p.pk for p in piezas]).update(estado=Pieza.RESERVADA)
            PiezaPedido.objects.bulk_create([PiezaPedido(item=item, pieza=p) for p in piezas])

    piezas = Pieza.objects.filter(venta__item__pedido=pedido)
    tandas = set(piezas.values_list('tanda_id', flat=True))
    piezas.update(estado=Pieza.VENDIDA)
    for t in Tanda.objects.filter(pk__in=tandas):
        actualizar_estado_agotada(t)

    pedido.estado = Pedido.PAGADO
    pedido.pagado_en = timezone.now()
    pedido.motivo_cancelacion = ''
    pedido.novedad = True                                   # aviso en el panel del productor
    pedido.save(update_fields=['estado', 'pagado_en', 'motivo_cancelacion', 'novedad',
                               'mp_datos', 'mp_payment_id', 'mp_metodo'])
    ItemCarrito.objects.filter(user=pedido.cliente,
                               receta__in=pedido.items.values('receta')).delete()
    return pedido


@transaction.atomic
def cancelar_pedido_pendiente(pedido, motivo):
    pedido = Pedido.objects.select_for_update().get(pk=pedido.pk)
    if pedido.estado != Pedido.PENDIENTE_PAGO:
        return pedido
    _liberar_piezas(pedido)
    pedido.estado = Pedido.CANCELADO
    pedido.motivo_cancelacion = motivo[:200]
    pedido.save(update_fields=['estado', 'motivo_cancelacion'])
    transaction.on_commit(lambda: mp.cancelar_orden(pedido.mp_order_id))
    return pedido


@transaction.atomic
def cancelar_pedido_pagado(pedido, motivo, devolver_al_stock=True):
    """El productor cancela un pedido ya cobrado (hay que reintegrar a mano)."""
    pedido = Pedido.objects.select_for_update().get(pk=pedido.pk)
    if pedido.estado in (Pedido.CANCELADO, Pedido.VENCIDO, Pedido.ENTREGADO, Pedido.PENDIENTE_PAGO):
        raise ValidationError('Este pedido no se puede cancelar desde acá.')
    piezas = Pieza.objects.filter(venta__item__pedido=pedido)
    tandas = set(piezas.values_list('tanda_id', flat=True))
    piezas.update(estado=Pieza.DISPONIBLE if devolver_al_stock else Pieza.BAJA)
    PiezaPedido.objects.filter(item__pedido=pedido).delete()
    for t in Tanda.objects.filter(pk__in=tandas):
        actualizar_estado_agotada(t)
    pedido.estado = Pedido.CANCELADO
    pedido.reintegro_pendiente = True
    pedido.motivo_cancelacion = motivo[:200]
    pedido.save(update_fields=['estado', 'reintegro_pendiente', 'motivo_cancelacion'])
    if devolver_al_stock:
        transaction.on_commit(enviar_avisos_stock)
    return pedido


def cancelar_reservas_de_tanda(tanda):
    """Al pausar una tanda se cancelan los QR pendientes que tenían piezas de ella."""
    pendientes = Pedido.objects.filter(estado=Pedido.PENDIENTE_PAGO,
                                       items__asignaciones__pieza__tanda=tanda).distinct()
    for p in pendientes:
        cancelar_pedido_pendiente(p, 'Un producto dejó de estar disponible')
    return len(pendientes)


def sincronizar_pago(pedido):
    """Pregunta a Mercado Pago cómo está la orden (sirve aunque no lleguen los webhooks)."""
    if pedido.estado == Pedido.PENDIENTE_PAGO and pedido.mp_order_id:
        orden = mp.obtener_orden(pedido.mp_order_id)
        estado = orden.get('status') if orden else None
        if estado == 'processed':
            return confirmar_pago(pedido, orden)
        if estado in ('expired', 'canceled'):
            cancelar_pedido_pendiente(pedido, 'El QR venció o se canceló en Mercado Pago')
    liberar_reservas_vencidas()
    pedido.refresh_from_db()
    return pedido


# ---------------------------------------------------------------------------
# Avisos de stock ("avisame cuando haya")
# ---------------------------------------------------------------------------

def enviar_avisos_stock():
    """Avisa a quienes esperaban un producto que ahora tiene piezas a la venta."""
    pendientes = AvisoStock.objects.filter(avisado_en__isnull=True).select_related('user', 'receta')
    if not pendientes.exists():
        return 0
    con_stock = set(stock_por_receta([a.receta for a in pendientes]).keys())
    enviados = 0
    for aviso in pendientes:
        if aviso.receta_id not in con_stock:
            continue
        if aviso.user.email:
            try:
                send_mail(
                    f'Volvió a haber {aviso.receta.nombre} - Iurko Chacinados',
                    f'¡Hola {aviso.user.first_name or aviso.user.username}!\n\n'
                    f'Ya tenemos {aviso.receta.nombre} de una tanda nueva.\n'
                    f'Precio actual: ${aviso.receta.precio_kg:,.0f} por kg.\n\n'
                    f'Pedilo en: {reverse("tienda:producto", args=[aviso.receta.slug])}\n',
                    None, [aviso.user.email], fail_silently=True,
                )
            except Exception:   # el aviso en la web igual queda
                log.exception('No se pudo mandar el correo de aviso %s', aviso.pk)
        aviso.avisado_en = timezone.now()
        aviso.save(update_fields=['avisado_en'])
        enviados += 1
    return enviados


def pedir_aviso(user, receta):
    aviso, creado = AvisoStock.objects.get_or_create(user=user, receta=receta, avisado_en__isnull=True)
    return creado
