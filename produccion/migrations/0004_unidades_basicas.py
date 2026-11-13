from django.db import migrations

UNIDADES = [
    ('Kilogramo', 'kg'),
    ('Gramo', 'g'),
    ('Unidad', 'u'),
    ('Litro', 'l'),
    ('Mililitro', 'ml'),
    ('Metro', 'm'),
]


def crear_unidades(apps, schema_editor):
    UnidadMedida = apps.get_model('produccion', 'UnidadMedida')
    for nombre, abrev in UNIDADES:
        if not UnidadMedida.objects.filter(abreviatura=abrev).exists() and \
                not UnidadMedida.objects.filter(nombre=nombre).exists():
            UnidadMedida.objects.create(nombre=nombre, abreviatura=abrev)


class Migration(migrations.Migration):
    dependencies = [('produccion', '0003_receta_stock_minimo_piezas')]
    operations = [migrations.RunPython(crear_unidades, migrations.RunPython.noop)]
