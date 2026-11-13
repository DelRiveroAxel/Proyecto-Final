"""Enlaces de WhatsApp (wa.me) a partir de teléfonos argentinos escritos como venga.

wa.me necesita el número internacional sin '+': 54 + 9 (celular) + característica + número.
Acepta: '3755 412345', '03755-15-412345', '+54 9 3755 412345', '(3755) 15412345', etc.
"""

from urllib.parse import quote


def numero_wa(telefono):
    d = ''.join(c for c in str(telefono or '') if c.isdigit())
    if d.startswith('00'):
        d = d[2:]
    if d.startswith('54'):
        d = d[2:]
    if d.startswith('9') and len(d) == 11:
        d = d[1:]
    d = d.lstrip('0')
    # el '15' de los celulares va después de la característica (2 a 4 dígitos) y no se usa en WhatsApp
    if len(d) == 12:
        for i in (2, 3, 4):
            if d[i:i + 2] == '15':
                d = d[:i] + d[i + 2:]
                break
    if len(d) != 10 or len(set(d)) == 1:      # no es un número argentino válido (o es de ejemplo)
        return ''
    return '549' + d


def enlace(telefono, texto=''):
    n = numero_wa(telefono)
    if not n:
        return ''
    return f'https://wa.me/{n}' + (f'?text={quote(texto)}' if texto else '')
