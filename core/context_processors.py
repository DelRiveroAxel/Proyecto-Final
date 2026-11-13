def marca(request):
    """Datos de la página Nosotros disponibles en todas las plantillas (año, contacto)."""
    from django.utils.functional import SimpleLazyObject

    from .models import PaginaNosotros
    return {'marca': SimpleLazyObject(PaginaNosotros.obtener)}
