def carrito(request):
    """Cantidad de piezas en el carrito y avisos de stock sin ver (para la cabecera)."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or user.is_staff:
        return {}
    from django.db.models import Sum

    from .models import AvisoStock, ItemCarrito
    total = ItemCarrito.objects.filter(user=user).aggregate(s=Sum('cantidad'))['s'] or 0
    avisos = list(AvisoStock.objects.filter(user=user, avisado_en__isnull=False, visto=False)
                  .select_related('receta')[:5])
    return {'carrito_cantidad': total, 'avisos_nuevos': avisos}
