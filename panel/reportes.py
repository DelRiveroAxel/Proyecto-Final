"""Reportes del panel: producción, ventas, ganancia estimada, insumos y mermas por período."""

from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.utils import timezone

from produccion.models import ConsumoMateriaPrima, Pieza, Receta, Tanda
from tienda.models import Pedido, PedidoItem, PiezaPedido

D0 = Decimal('0')
VENTA = [Pedido.PAGADO, Pedido.PREPARANDO, Pedido.LISTO, Pedido.ENTREGADO]   # pagados y no cancelados
MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']

PRESETS = [('mes', 'Este mes'), ('mes_pasado', 'Mes pasado'), ('3m', 'Últimos 3 meses'),
           ('anio', 'Este año'), ('12m', 'Últimos 12 meses')]


def _sumar_meses(d, n):
    m = d.month - 1 + n
    a, m = d.year + m // 12, m % 12 + 1
    return date(a, m, 1)


def periodo(get):
    """(desde, hasta, preset) a partir de la URL. Por defecto: este mes."""
    hoy = timezone.localdate()
    preset = get.get('periodo', '')
    try:
        if get.get('desde') and get.get('hasta'):
            d, h = date.fromisoformat(get['desde']), date.fromisoformat(get['hasta'])
            if d > h:
                d, h = h, d
            return d, h, ''
    except ValueError:
        pass
    inicio_mes = hoy.replace(day=1)
    if preset == 'mes_pasado':
        h = inicio_mes - timedelta(days=1)
        return h.replace(day=1), h, preset
    if preset == '3m':
        return _sumar_meses(inicio_mes, -2), hoy, preset
    if preset == 'anio':
        return date(hoy.year, 1, 1), hoy, preset
    if preset == '12m':
        return _sumar_meses(inicio_mes, -11), hoy, preset
    return inicio_mes, hoy, 'mes'


def _meses_entre(d, h):
    m, fin = d.replace(day=1), h.replace(day=1)
    while m <= fin:
        yield m
        m = _sumar_meses(m, 1)


def calcular(desde, hasta):
    pedidos = Pedido.objects.filter(estado__in=VENTA, pagado_en__date__gte=desde, pagado_en__date__lte=hasta)
    tandas = Tanda.objects.filter(fecha_produccion__gte=desde, fecha_produccion__lte=hasta)

    filas = OrderedDict((r.pk, {'receta': r, 'kg_prod': D0, 'piezas_prod': 0, 'costo_prod': D0, 'piezas_vend': 0,
                                'g_vend': 0, 'ventas': D0, 'costo_vend': D0, 'g_baja': 0, 'sin_costo': False})
                        for r in Receta.objects.order_by('nombre'))

    # Producción del período (las anuladas cuentan como merma, no como producción vendible)
    for t in tandas.exclude(estado=Tanda.ANULADA).values('receta_id').annotate(
            kg=Sum('kg_producidos'), pz=Sum('cantidad_piezas'), costo=Sum('costo_insumos')):
        f = filas[t['receta_id']]
        f['kg_prod'], f['piezas_prod'], f['costo_prod'] = t['kg'] or D0, t['pz'] or 0, t['costo'] or D0
    for b in (Pieza.objects.filter(tanda__in=tandas, estado=Pieza.BAJA)
              .values('tanda__receta_id').annotate(g=Sum('peso_g'))):
        filas[b['tanda__receta_id']]['g_baja'] = b['g'] or 0

    # Ventas del período (por fecha de pago)
    for it in (PedidoItem.objects.filter(pedido__in=pedidos).values('receta_id')
               .annotate(pz=Sum('cantidad'), g=Sum('peso_g'), plata=Sum('subtotal'))):
        f = filas[it['receta_id']]
        f['piezas_vend'], f['g_vend'], f['ventas'] = it['pz'] or 0, it['g'] or 0, it['plata'] or D0

    # Costo de lo vendido = peso de cada pieza × costo por kg de su tanda
    for a in (PiezaPedido.objects.filter(item__pedido__in=pedidos)
              .values('item__receta_id', 'pieza__peso_g', 'pieza__tanda__costo_insumos',
                      'pieza__tanda__kg_producidos', 'pieza__tanda__desconto_insumos')):
        f = filas[a['item__receta_id']]
        kg_t = a['pieza__tanda__kg_producidos'] or D0
        if not a['pieza__tanda__desconto_insumos'] or not kg_t:
            f['sin_costo'] = True
            continue
        f['costo_vend'] += a['pieza__tanda__costo_insumos'] / kg_t * Decimal(a['pieza__peso_g']) / 1000

    # Ventas por fuera de la web (WhatsApp, en el local, feria...): piezas marcadas a mano, por fecha de venta
    externas = list(Pieza.objects.filter(estado=Pieza.VENDIDA, venta__isnull=True,
                                         venta_externa_fecha__gte=desde, venta_externa_fecha__lte=hasta)
                    .order_by('venta_externa_fecha', 'codigo')
                    .values('tanda__receta_id', 'tanda__receta__nombre', 'codigo', 'motivo_baja', 'peso_g',
                            'venta_externa_fecha', 'venta_externa_monto',
                            'tanda__costo_insumos', 'tanda__kg_producidos', 'tanda__desconto_insumos'))
    for x in externas:
        f = filas[x['tanda__receta_id']]
        f['piezas_vend'] += 1
        f['g_vend'] += x['peso_g']
        f['ventas'] += x['venta_externa_monto'] or D0
        kg_t = x['tanda__kg_producidos'] or D0
        if not x['tanda__desconto_insumos'] or not kg_t:
            f['sin_costo'] = True
        else:
            f['costo_vend'] += x['tanda__costo_insumos'] / kg_t * Decimal(x['peso_g']) / 1000

    recetas = []
    for f in filas.values():
        if not (f['kg_prod'] or f['ventas'] or f['g_baja']):
            continue
        f['costo_vend'] = f['costo_vend'].quantize(Decimal('1'))
        f['ganancia'] = f['ventas'] - f['costo_vend']
        f['margen'] = round(100 * f['ganancia'] / f['ventas']) if f['ventas'] else None
        f['kg_vend'] = Decimal(f['g_vend']) / 1000
        f['kg_baja'] = Decimal(f['g_baja']) / 1000
        recetas.append(f)
    recetas.sort(key=lambda f: f['ventas'], reverse=True)

    tot = {k: sum((f[k] for f in recetas), D0) for k in
           ['kg_prod', 'costo_prod', 'ventas', 'costo_vend', 'ganancia', 'kg_vend', 'kg_baja']}
    tot['piezas_vend'] = sum(f['piezas_vend'] for f in recetas)
    tot['piezas_prod'] = sum(f['piezas_prod'] for f in recetas)
    tot['pedidos'] = pedidos.count()
    tot['clientes'] = pedidos.values('cliente').distinct().count()
    tot['fuera_piezas'] = len(externas)
    tot['fuera_ventas'] = sum((x['venta_externa_monto'] or D0 for x in externas), D0).quantize(Decimal('1'))
    tot['ventas_web'] = tot['ventas'] - tot['fuera_ventas']
    tot['ticket'] = (tot['ventas_web'] / tot['pedidos']).quantize(Decimal('1')) if tot['pedidos'] else D0
    tot['margen'] = round(100 * tot['ganancia'] / tot['ventas']) if tot['ventas'] else None
    tot['sin_costo'] = any(f['sin_costo'] for f in recetas)
    max_ventas = max((f['ventas'] for f in recetas), default=D0)
    for f in recetas:
        f['pct'] = round(100 * f['ventas'] / max_ventas) if max_ventas else 0

    # Por mes
    meses = OrderedDict((m, {'mes': m, 'etiqueta': f'{MESES[m.month - 1]} {m:%y}', 'ventas': D0, 'kg_vend': D0,
                             'kg_prod': D0, 'pedidos': 0}) for m in _meses_entre(desde, hasta))
    for p in pedidos.annotate(g=Sum('items__peso_g')).values('pagado_en', 'total', 'g'):
        m = meses.get(timezone.localtime(p['pagado_en']).date().replace(day=1))
        if m:
            m['ventas'] += p['total']
            m['kg_vend'] += Decimal(p['g'] or 0) / 1000
            m['pedidos'] += 1
    for x in externas:
        m = meses.get(x['venta_externa_fecha'].replace(day=1))
        if m:
            m['ventas'] += x['venta_externa_monto'] or D0
            m['kg_vend'] += Decimal(x['peso_g']) / 1000
    for t in tandas.exclude(estado=Tanda.ANULADA).values('fecha_produccion', 'kg_producidos'):
        m = meses.get(t['fecha_produccion'].replace(day=1))
        if m:
            m['kg_prod'] += t['kg_producidos']
    meses = list(meses.values())
    tope_v = max((m['ventas'] for m in meses), default=D0) or 1
    tope_k = max((max(m['kg_prod'], m['kg_vend']) for m in meses), default=D0) or 1
    for m in meses:
        m['h_ventas'] = round(100 * m['ventas'] / tope_v)
        m['h_prod'] = round(100 * m['kg_prod'] / tope_k)
        m['h_vend'] = round(100 * m['kg_vend'] / tope_k)

    # Materias primas usadas en la producción del período
    insumos = list(ConsumoMateriaPrima.objects.filter(tanda__in=tandas)
                   .values('lote__tipo__nombre', 'lote__tipo__unidad__abreviatura')
                   .annotate(cant=Sum('cantidad'), costo=Sum(F('cantidad') * F('precio_unitario')))
                   .order_by('-costo'))

    clientes = list(pedidos.values('cliente__first_name', 'cliente__last_name', 'cliente__username', 'cliente__email')
                    .annotate(n=Count('id'), plata=Sum('total')).order_by('-plata')[:10])
    for c in clientes:
        c['nombre'] = f"{c['cliente__first_name']} {c['cliente__last_name']}".strip() or c['cliente__username']

    return {'recetas': recetas, 'tot': tot, 'meses': meses, 'insumos': insumos, 'clientes': clientes,
            'fuera': ventas_fuera(externas, tot['ventas'])}


def _canal_y_cliente(detalle):
    """'Venta por fuera de la web · WhatsApp · Marta' -> ('WhatsApp', 'Marta')."""
    partes = [p.strip() for p in (detalle or '').split(' · ')]
    if partes and partes[0].lower().startswith('venta por fuera'):
        partes = partes[1:]
    return (partes[0] if partes else 'Sin indicar'), ' · '.join(partes[1:])


def ventas_fuera(externas, ventas_totales):
    """Resumen de lo vendido por fuera de la web (WhatsApp, en el local, ferias...) en el período."""
    canales, recetas, detalle = OrderedDict(), OrderedDict(), []
    for x in externas:
        canal, cliente = _canal_y_cliente(x['motivo_baja'])
        monto = x['venta_externa_monto'] or D0
        for grupo, clave in ((canales, canal), (recetas, x['tanda__receta__nombre'])):
            g = grupo.setdefault(clave, {'nombre': clave, 'piezas': 0, 'g': 0, 'total': D0})
            g['piezas'] += 1
            g['g'] += x['peso_g']
            g['total'] += monto
        detalle.append({'fecha': x['venta_externa_fecha'], 'canal': canal, 'cliente': cliente,
                        'receta': x['tanda__receta__nombre'], 'codigo': x['codigo'], 'peso_g': x['peso_g'],
                        'monto': monto})
    total = sum((d['monto'] for d in detalle), D0)
    grupos = []
    for grupo in (canales, recetas):
        filas = sorted(grupo.values(), key=lambda g: g['total'], reverse=True)
        for g in filas:
            g['kg'] = Decimal(g['g']) / 1000
            g['total'] = g['total'].quantize(Decimal('1'))
            g['pct'] = round(100 * g['total'] / total) if total else 0
        grupos.append(filas)
    g_total = sum(d['peso_g'] for d in detalle)
    return {
        'piezas': len(detalle), 'kg': Decimal(g_total) / 1000, 'total': total.quantize(Decimal('1')),
        'promedio_kg': (total * 1000 / g_total).quantize(Decimal('1')) if g_total else D0,
        'pct_ventas': round(100 * total / ventas_totales) if ventas_totales else 0,
        'canales': grupos[0], 'recetas': grupos[1], 'detalle': detalle,
    }
