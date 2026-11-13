"""Trazabilidad: de una materia prima a los clientes que recibieron el producto, y al revés.

Hacia adelante:  lote de materia prima → tandas que lo usaron → piezas → pedidos → clientes.
Hacia atrás:     pieza / tanda → lotes de materia prima usados → compra → proveedor.
"""

from collections import OrderedDict

from django.db.models import Count, Q, Sum

from produccion.models import ConsumoMateriaPrima, Pieza
from tienda.models import Pedido, PiezaPedido


def resumen_piezas(tanda):
    return tanda.piezas.aggregate(
        total=Count('id'),
        libres=Count('id', filter=Q(estado=Pieza.DISPONIBLE)),
        reservadas=Count('id', filter=Q(estado=Pieza.RESERVADA)),
        vendidas=Count('id', filter=Q(estado=Pieza.VENDIDA)),
        baja=Count('id', filter=Q(estado=Pieza.BAJA)),
        fuera=Count('id', filter=Q(estado=Pieza.VENDIDA, venta_externa_fecha__isnull=False)),
        kg_vendidos_g=Sum('peso_g', filter=Q(estado=Pieza.VENDIDA)),
    )


def clientes_de(tandas):
    """Clientes que recibieron (o tienen reservadas) piezas de esas tandas, agrupados por pedido.
    Devuelve una lista de dicts ordenada por fecha de pedido."""
    asign = (PiezaPedido.objects.filter(pieza__tanda__in=tandas)
             .select_related('pieza__tanda__receta', 'item__pedido__cliente__cliente')
             .order_by('item__pedido__creado', 'pieza__codigo'))
    pedidos = OrderedDict()
    for a in asign:
        p = a.item.pedido
        fila = pedidos.get(p.pk)
        if fila is None:
            u = p.cliente
            datos = getattr(u, 'cliente', None)
            fila = pedidos[p.pk] = {
                'pedido': p, 'usuario': u, 'telefono': getattr(datos, 'telefono', ''),
                'localidad': getattr(datos, 'localidad', ''), 'piezas': [],
                'confirmado': p.estado != Pedido.PENDIENTE_PAGO,
            }
        fila['piezas'].append(a.pieza)
    return list(pedidos.values())


def fuera_de(tandas):
    """Piezas de esas tandas vendidas por fuera de la web (WhatsApp, en el local, feria...).
    No tienen pedido: el detalle (canal y cliente, si se anotó) queda en la pieza."""
    return list(Pieza.objects.filter(tanda__in=tandas, estado=Pieza.VENDIDA, venta__isnull=True,
                                     venta_externa_fecha__isnull=False)
                .select_related('tanda__receta').order_by('venta_externa_fecha', 'codigo'))


def personas(filas):
    """Clientes sin repetir (una persona puede tener varios pedidos)."""
    vistos = OrderedDict()
    for f in filas:
        u = f['usuario']
        d = vistos.setdefault(u.pk, {'usuario': u, 'telefono': f['telefono'], 'pedidos': [], 'piezas': 0})
        d['pedidos'].append(f['pedido'])
        d['piezas'] += len(f['piezas'])
    return list(vistos.values())


def hacia_adelante(lote):
    """Desde un lote de materia prima: en qué tandas se usó y a quién llegaron sus piezas."""
    consumos = (ConsumoMateriaPrima.objects.filter(lote=lote)
                .select_related('tanda__receta').order_by('tanda__fecha_produccion', 'tanda__numero_dia'))
    tandas = []
    for c in consumos:
        t = c.tanda
        t.usado = c.cantidad
        t.resumen = resumen_piezas(t)
        tandas.append(t)
    filas = clientes_de(tandas)
    return {'tandas': tandas, 'pedidos': filas, 'personas': personas(filas), 'fuera': fuera_de(tandas),
            'activas': [t for t in tandas if t.estado == t.ACTIVA],
            'pausadas': [t for t in tandas if t.estado == t.PAUSADA]}


def hacia_atras(tanda):
    """Desde una tanda: qué lotes de materia prima se usaron, de qué compra y proveedor."""
    return (tanda.consumos.select_related('lote__tipo__unidad', 'lote__compra__proveedor')
            .order_by('lote__tipo__nombre', 'lote__compra__fecha'))
