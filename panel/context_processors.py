def alertas(request):
    """Alertas del menú del panel, con el motivo de cada una (se muestra arriba de cada lista)."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and user.is_staff and request.path.startswith('/panel/')):
        return {}
    from django.db.models import Count, Min, Q, Sum
    from django.utils import timezone

    from produccion.models import Pieza, Receta, Tanda, TipoMateriaPrima
    from core.models import ConsultaContacto
    from tienda.models import Pedido

    hoy = timezone.localdate()
    libre = Q(tandas__piezas__estado=Pieza.DISPONIBLE) & (
        Q(tandas__fecha_vencimiento__isnull=True) | Q(tandas__fecha_vencimiento__gt=hoy))   # lo vencido no cuenta
    recetas = (Receta.objects.filter(publicada=True).annotate(
        stock=Count('tandas__piezas', filter=libre & Q(tandas__estado=Tanda.ACTIVA, tandas__fecha_disponible__lte=hoy)),
        estacionando=Count('tandas__piezas', filter=libre & Q(tandas__estado=Tanda.ACTIVA, tandas__fecha_disponible__gt=hoy)),
        pausadas=Count('tandas__piezas', filter=libre & Q(tandas__estado=Tanda.PAUSADA)),
        listo_el=Min('tandas__fecha_disponible', filter=Q(tandas__estado=Tanda.ACTIVA, tandas__fecha_disponible__gt=hoy)),
        n_tandas=Count('tandas', distinct=True),
    ))
    recetas_bajo = []
    for r in recetas:
        if r.stock >= r.stock_minimo_piezas:
            continue
        if r.stock:
            r.motivo = f'Quedan {r.stock} pieza{"s" if r.stock != 1 else ""} a la venta y el aviso es con menos de {r.stock_minimo_piezas}.'
        elif not r.n_tandas:
            r.motivo = 'Todavía no se cargó ninguna tanda de esta receta.'
        else:
            r.motivo = 'No quedan piezas a la venta.'
        extras = []
        if r.estacionando:
            extras.append(f'{r.estacionando} estacionando hasta el {r.listo_el:%d/%m}')
        if r.pausadas:
            extras.append(f'{r.pausadas} en tandas pausadas')
        r.detalle = ' · '.join(extras)
        r.gravedad = 'err' if not r.stock else 'warn'
        recetas_bajo.append(r)
    insumos_bajo = [t for t in TipoMateriaPrima.objects.filter(activo=True, stock_minimo__gt=0)
                    .annotate(stock_anotado=Sum('lotes__cantidad_disponible')).select_related('unidad')
                    if t.bajo_minimo]
    from produccion.services import tandas_por_vencer
    vencen = tandas_por_vencer()
    return {'alertas': {
        'recetas_bajo': recetas_bajo,
        'vencen': vencen,
        'vencidas': sum(1 for t in vencen if t.vencida),
        'inventario': len(recetas_bajo) + len(vencen),
        'insumos_bajo': insumos_bajo,
        'pedidos_nuevos': Pedido.objects.filter(novedad=True).count(),
        'consultas_nuevas': ConsultaContacto.objects.filter(respondida=False).count(),
    }}
