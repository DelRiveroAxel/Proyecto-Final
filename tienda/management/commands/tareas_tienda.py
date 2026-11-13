"""Tareas periódicas: liberar reservas vencidas y avisar stock nuevo.

Programalo cada 5 minutos (Programador de tareas de Windows o cron):
    python manage.py tareas_tienda
La web igual las ejecuta sola cuando alguien entra al catálogo.
"""
from django.core.management.base import BaseCommand

from tienda.services import enviar_avisos_stock, liberar_reservas_vencidas


class Command(BaseCommand):
    help = 'Libera reservas de QR vencidos y envía los avisos de "volvió a haber stock".'

    def handle(self, *args, **opts):
        lib = liberar_reservas_vencidas()
        av = enviar_avisos_stock()
        self.stdout.write(self.style.SUCCESS(f'Reservas liberadas: {lib} · Avisos enviados: {av}'))
