from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html

register = template.Library()


def _miles(n, decimales=0):
    texto = f'{n:,.{decimales}f}'
    return texto.replace(',', 'X').replace('.', ',').replace('X', '.')


@register.filter
def pesos(valor, decimales=0):
    """12000 -> $ 12.000"""
    try:
        return f'$ {_miles(Decimal(valor), int(decimales))}'
    except (InvalidOperation, TypeError, ValueError):
        return valor


@register.filter
def num(valor, decimales=0):
    try:
        return _miles(Decimal(valor), int(decimales))
    except (InvalidOperation, TypeError, ValueError):
        return valor


@register.filter
def cant(valor):
    """Cantidad sin ceros de más: 19.000 -> 19 · 0.030 -> 0,03 · 1500.5 -> 1.500,5"""
    try:
        d = Decimal(valor)
    except (InvalidOperation, TypeError, ValueError):
        return valor
    texto = format(d.normalize(), 'f')
    decimales = len(texto.split('.')[1]) if '.' in texto else 0
    return _miles(d, decimales)


@register.filter
def kg(gramos):
    """1040 (g) -> 1,04 kg"""
    try:
        return f'{_miles(Decimal(gramos) / 1000, 2)} kg'
    except (InvalidOperation, TypeError, ValueError):
        return gramos


@register.filter
def codigo(valor):
    """Muestra el código numérico resaltando sus partes (sin guiones):
    fecha DDMMAAAA en gris, n.º de tanda y n.º de pieza en bordó."""
    c = str(valor)
    if len(c) == 14:
        return format_html('<span class="ik-code" title="Fecha {}/{}/{} · tanda {} · pieza {}">'
                           '<span class="f">{}</span><span class="n">{}</span><span class="f">{}</span></span>',
                           c[:2], c[2:4], c[4:8], c[8:10], c[10:], c[:8], c[8:10], c[10:])
    if len(c) == 10:
        return format_html('<span class="ik-code" title="Fecha {}/{}/{} · tanda {}">'
                           '<span class="f">{}</span><span class="n">{}</span></span>',
                           c[:2], c[2:4], c[4:8], c[8:], c[:8], c[8:])
    return c


@register.filter
def peso(gramos):
    """Peso exacto para etiquetas: 352 -> '352 g'; 1250 -> '1,250 kg'."""
    try:
        g = int(gramos)
    except (TypeError, ValueError):
        return gramos
    if g < 1000:
        return f'{g} g'
    return f'{_miles(Decimal(g) / 1000, 3)} kg'


@register.simple_tag
def whatsapp(telefono, texto=''):
    """{% whatsapp tel "Hola" as url %} -> enlace wa.me listo (vacío si el número no sirve)."""
    from core.whatsapp import enlace
    return enlace(telefono, texto)
