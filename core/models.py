from django.db import models


class PaginaNosotros(models.Model):
    """Página "Nosotros" de la tienda. Hay una sola: se edita desde el panel del productor.
    Los textos que vienen por defecto son de ejemplo: reemplazá lo que está entre [corchetes]."""

    titulo = models.CharField(max_length=120, default='Nuestra historia')
    bajada = models.CharField(
        max_length=250,
        default='Tres generaciones elaborando chacinados artesanales con recetas de familia.')
    anio_fundacion = models.PositiveIntegerField('Año de fundación', default=1978)
    historia = models.TextField(default=(
        'Todo empezó en [año] cuando [nombre del fundador] llegó a [localidad] con las recetas que '
        'había aprendido de su familia en [lugar de origen]. Lo que comenzó como una producción para '
        'la familia y los vecinos se convirtió, con los años, en Iurko Chacinados.\n\n'
        'Hoy seguimos elaborando cada pieza a mano, en tandas chicas, respetando los tiempos de '
        'estacionamiento que cada producto necesita.'))
    mision = models.TextField('Lo que nos mueve', default=(
        'Llevar a tu mesa chacinados hechos como antes: carne seleccionada, condimentos naturales '
        'y paciencia. Nada de apuros ni atajos.'))
    valores = models.TextField(
        'Valores (uno por línea)',
        default='Recetas de familia\nMateria prima de productores locales\nSin conservantes artificiales\nCada pieza con su código de origen')
    foto = models.ImageField(upload_to='nosotros/', blank=True)
    equipo = models.TextField('Quiénes somos', blank=True, default=(
        '[Nombre] — Maestro chacinador\n[Nombre] — Producción y calidad\n[Nombre] — Atención a clientes'))
    direccion = models.CharField('Dirección', max_length=200, blank=True, default='[Calle y número], [Localidad], Misiones')
    horario = models.CharField('Horario de atención', max_length=200, blank=True, default='Lunes a viernes de 8 a 12 y de 16 a 20 · Sábados de 8 a 13')
    telefono = models.CharField('Teléfono / WhatsApp', max_length=40, blank=True, default='[3755 000000]')
    email = models.EmailField('Correo', blank=True, default='contacto@iurko.com')
    instagram = models.CharField('Instagram (sin @)', max_length=60, blank=True, default='iurkochacinados')
    facebook = models.CharField('Facebook (usuario o página)', max_length=100, blank=True)
    mostrar_sello = models.BooleanField('Mostrar el sello en el carrusel', default=True)
    sello_linea = models.CharField('Texto chico del sello', max_length=30, default='Garantía real', blank=True)
    mensaje_whatsapp = models.CharField(
        'Mensaje inicial de WhatsApp', max_length=200, blank=True,
        default='¡Hola! Tengo una consulta sobre los productos de Iurko Chacinados.',
        help_text='El texto que ya aparece escrito cuando el cliente toca el botón de WhatsApp.')
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pagina_nosotros'
        verbose_name = 'Página Nosotros'
        verbose_name_plural = 'Página Nosotros'

    def __str__(self):
        return self.titulo

    @classmethod
    def obtener(cls):
        obj = cls.objects.first()
        return obj or cls.objects.create()

    def lista_valores(self):
        return [v.strip() for v in self.valores.splitlines() if v.strip()]

    def lista_equipo(self):
        filas = []
        for linea in self.equipo.splitlines():
            if not linea.strip():
                continue
            nombre, _, rol = linea.partition('—') if '—' in linea else linea.partition('-')
            filas.append((nombre.strip(), rol.strip()))
        return filas

    @property
    def whatsapp_link(self):
        from .whatsapp import enlace
        if '000000' in ''.join(c for c in self.telefono if c.isdigit()):   # número de ejemplo
            return ''
        return enlace(self.telefono, self.mensaje_whatsapp)


class PreguntaFrecuente(models.Model):
    pregunta = models.CharField(max_length=200)
    respuesta = models.TextField()
    orden = models.PositiveSmallIntegerField(default=0)
    visible = models.BooleanField(default=True)

    class Meta:
        db_table = 'preguntas_frecuentes'
        verbose_name = 'Pregunta frecuente'
        verbose_name_plural = 'Preguntas frecuentes'
        ordering = ['orden', 'id']

    def __str__(self):
        return self.pregunta



ICONOS_SLIDE = [
    ('', 'Sin ícono'),
    ('local_fire_department', 'Fuego / ahumado'),
    ('eco', 'Hoja / natural'),
    ('workspace_premium', 'Medalla / calidad'),
    ('restaurant', 'Cubiertos'),
    ('star', 'Estrella'),
    ('schedule', 'Reloj / estacionado'),
    ('ac_unit', 'Frío / cámara'),
    ('celebration', 'Fiesta / promoción'),
    ('local_shipping', 'Envío'),
]


class SlideInicio(models.Model):
    """Una diapositiva del carrusel de la página de inicio de la tienda."""

    etiqueta = models.CharField('Texto chico de arriba', max_length=40, blank=True,
                                help_text='Ej.: "Ahumado en leña de quebracho".')
    icono = models.CharField('Ícono', max_length=40, choices=ICONOS_SLIDE, default='local_fire_department', blank=True)
    titulo = models.CharField('Título', max_length=60)
    texto = models.CharField('Texto', max_length=200, blank=True)
    imagen = models.ImageField('Foto de fondo', upload_to='carrusel/', blank=True,
                               help_text='Opcional. Si no subís una, usa la foto del producto elegido.')
    receta = models.ForeignKey('produccion.Receta', on_delete=models.SET_NULL, null=True, blank=True,
                               verbose_name='Producto', help_text='El botón lleva a este producto.')
    boton_texto = models.CharField('Texto del botón', max_length=30, default='Ver producto', blank=True)
    boton_url = models.CharField('…o el botón lleva a esta dirección', max_length=200, blank=True,
                                 help_text='Solo si no elegiste producto. Ej.: /nosotros/')
    orden = models.PositiveSmallIntegerField(default=0)
    activo = models.BooleanField('Visible', default=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'carrusel_inicio'
        verbose_name = 'Diapositiva del inicio'
        verbose_name_plural = 'Carrusel del inicio'
        ordering = ['orden', 'id']

    def __str__(self):
        return self.titulo

    @property
    def enlace(self):
        if self.receta_id:
            from django.urls import reverse
            return reverse('tienda:producto', args=[self.receta.slug])
        return self.boton_url

    @property
    def foto(self):
        if self.imagen:
            return self.imagen
        return self.receta.foto_principal if self.receta_id else None


class ConsultaContacto(models.Model):
    """Mensaje enviado desde el formulario de contacto de la tienda."""

    nombre = models.CharField(max_length=120)
    email = models.EmailField('Correo')
    telefono = models.CharField('Teléfono', max_length=40, blank=True)
    mensaje = models.TextField()
    usuario = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)
    correo_enviado = models.BooleanField(default=False)
    respondida = models.BooleanField(default=False)

    class Meta:
        db_table = 'consultas_contacto'
        verbose_name = 'Consulta'
        verbose_name_plural = 'Consultas'
        ordering = ['-creado']

    def __str__(self):
        return f'{self.nombre} · {self.creado:%d/%m/%Y}'
