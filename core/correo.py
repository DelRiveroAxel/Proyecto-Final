"""Envío de correos de Iurko: consultas de contacto y correo de prueba.

Si en el .env están EMAIL_USER y EMAIL_PASSWORD, los correos salen de verdad por SMTP (Gmail por defecto).
Si no, Django los muestra en la consola donde corre runserver (sirve para probar sin internet).
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse

log = logging.getLogger(__name__)


def correo_real():
    return bool(getattr(settings, 'EMAIL_REAL', False))


def destino_consultas():
    """A dónde llegan las consultas: el correo cargado en 'Nosotros' o, si no hay, la cuenta que envía."""
    from .models import PaginaNosotros
    return PaginaNosotros.obtener().email or getattr(settings, 'EMAIL_USER', '')


def enviar_consulta(consulta, request=None):
    """Manda la consulta al correo de Iurko con 'responder a' = el cliente. Devuelve True si se envió."""
    destino = destino_consultas()
    if not destino:
        return False
    enlace = request.build_absolute_uri(reverse('panel:consultas')) if request else ''
    cuerpo = (
        f'Nueva consulta desde la tienda\n\n'
        f'De: {consulta.nombre} <{consulta.email}>\n'
        f'Teléfono: {consulta.telefono or "—"}\n'
        f'Fecha: {consulta.creado:%d/%m/%Y %H:%M}\n\n'
        f'{consulta.mensaje}\n\n'
        f'— Respondé este correo y le llega directo al cliente.\n'
        + (f'— También la ves en el panel: {enlace}\n' if enlace else '')
    )
    msg = EmailMessage(subject=f'Consulta de {consulta.nombre} · Iurko Chacinados', body=cuerpo,
                       from_email=settings.DEFAULT_FROM_EMAIL, to=[destino], reply_to=[consulta.email])
    try:
        msg.send()
    except Exception:  # sin internet, clave mal puesta, etc.: la consulta igual queda guardada en el panel
        log.exception('No se pudo enviar la consulta %s', consulta.pk)
        return False
    consulta.correo_enviado = True
    consulta.save(update_fields=['correo_enviado'])
    return True


def enviar_prueba(destino):
    """Correo de prueba desde el panel. Devuelve (ok, detalle del error)."""
    msg = EmailMessage(
        subject='Prueba de correo · Iurko Chacinados',
        body='Si estás leyendo esto, el correo de la tienda funciona: las consultas y los enlaces '
             'para recuperar la contraseña van a llegar bien.\n\nIurko Chacinados',
        from_email=settings.DEFAULT_FROM_EMAIL, to=[destino])
    try:
        msg.send()
    except Exception as e:
        return False, str(e)
    return True, ''
