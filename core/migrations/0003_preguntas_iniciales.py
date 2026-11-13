from django.db import migrations

PREGUNTAS = [
    ('¿Por qué el precio es por kilo?',
     'Cada pieza es artesanal y pesa distinto. Pagás el peso exacto de las piezas que te tocan: lo ves antes '
     'de pagar, en la pantalla del QR.'),
    ('¿Cómo pago?',
     'Con el QR de Mercado Pago: lo escaneás desde la app de Mercado Pago o de tu banco. Al generar el QR te '
     'reservamos las piezas por 15 minutos; si no pagás en ese tiempo, vuelven al stock y podés generar otro.'),
    ('¿Qué piezas me tocan?',
     'Siempre las que se elaboraron primero. Cada pieza tiene un código de 14 números: los 8 primeros son la '
     'fecha de elaboración, los 2 siguientes la tanda de ese día y los 4 últimos el número de pieza.'),
    ('Se agotó lo que quería, ¿qué hago?',
     'Tocá "Avisame cuando haya" en el producto. No pagás nada por adelantado: te mandamos un correo apenas '
     'salga la próxima tanda, con el precio de ese momento.'),
    ('¿Hacen envíos?',
     '[Contá acá cómo se entregan los pedidos: retiro en el local, envío a domicilio, zonas y costos.]'),
]


def crear(apps, schema_editor):
    Pregunta = apps.get_model('core', 'PreguntaFrecuente')
    if not Pregunta.objects.exists():
        for i, (p, r) in enumerate(PREGUNTAS, start=1):
            Pregunta.objects.create(pregunta=p, respuesta=r, orden=i * 10)


class Migration(migrations.Migration):
    dependencies = [('core', '0002_preguntafrecuente_paginanosotros_mensaje_whatsapp')]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
