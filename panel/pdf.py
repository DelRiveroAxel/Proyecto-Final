"""PDF de los reportes y de la trazabilidad, armados con ReportLab (formato propio A4, no una captura de pantalla).

Cada PDF tiene: encabezado con la marca, título y período; resumen; gráficos; tablas con encabezado que se
repite en cada hoja; y pie con la fecha de generación y "Página X de Y".
"""

from decimal import Decimal
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

# Paleta (la misma de la web; las dos series de los gráficos están validadas para daltonismo)
BORDO = colors.HexColor('#7b2c2a')
BORDO_OSC = colors.HexColor('#3b1116')
CUERO = colors.HexColor('#a8703f')
TINTA = colors.HexColor('#2b1d1b')
GRIS = colors.HexColor('#6f625f')
LINEA = colors.HexColor('#e4d6d1')
FONDO = colors.HexColor('#f7efec')
SERIE_VEND = colors.HexColor('#a33b3f')
SERIE_PROD = colors.HexColor('#1a6c9c')
ROJO = colors.HexColor('#b3261e')

ANCHO, ALTO = A4
MARGEN = 15 * mm
UTIL = ANCHO - 2 * MARGEN - 12   # el marco de ReportLab deja 6 pt de cada lado

E = {
    'h2': ParagraphStyle('h2', fontName='Times-Bold', fontSize=13.5, leading=17, textColor=BORDO_OSC,
                         spaceBefore=10, spaceAfter=5),
    'h3': ParagraphStyle('h3', fontName='Helvetica-Bold', fontSize=9.5, leading=12, textColor=BORDO_OSC,
                         spaceBefore=7, spaceAfter=4),
    'p': ParagraphStyle('p', fontName='Helvetica', fontSize=8.8, leading=12, textColor=TINTA),
    'nota': ParagraphStyle('nota', fontName='Helvetica', fontSize=7.5, leading=10, textColor=GRIS),
    'celda': ParagraphStyle('celda', fontName='Helvetica', fontSize=8, leading=10, textColor=TINTA),
    'celda_b': ParagraphStyle('celda_b', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=TINTA),
    'celda_num': ParagraphStyle('celda_num', fontName='Helvetica', fontSize=8, leading=10, textColor=TINTA,
                                alignment=TA_RIGHT),
    'kpi_t': ParagraphStyle('kpi_t', fontName='Helvetica-Bold', fontSize=6.8, leading=8, textColor=GRIS),
    'kpi_v': ParagraphStyle('kpi_v', fontName='Times-Bold', fontSize=15, leading=18, textColor=BORDO_OSC),
    'kpi_s': ParagraphStyle('kpi_s', fontName='Helvetica', fontSize=7, leading=9, textColor=GRIS),
}


# --------------------------------------------------------------------------- formatos argentinos

def _miles(n):
    return f'{n:,}'.replace(',', '.')


def pesos(v):
    v = Decimal(v or 0).quantize(Decimal('1'))
    signo = '-' if v < 0 else ''
    return f'{signo}$ {_miles(abs(int(v)))}'


def num(v, dec=1):
    v = Decimal(v or 0)
    entero, _, frac = f'{abs(v):.{dec}f}'.partition('.')
    return ('-' if v < 0 else '') + _miles(int(entero)) + (f',{frac}' if dec else '')


def kg(v, dec=1):
    return f'{num(v, dec)} kg'


def pl(n, palabra, plural=None):
    """'1 cliente' / '3 clientes'."""
    return f'{n} {palabra if n == 1 else (plural or palabra + "s")}'


def nombre_cliente(u):
    v = getattr(u, 'nombre_visible', '')
    return v() if callable(v) else (v or u.get_username())


def esc(t):
    """Escapa texto para Paragraph (usa un mini-HTML)."""
    return str(t if t is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# --------------------------------------------------------------------------- hoja: encabezado y pie

class _Lienzo(rl_canvas.Canvas):
    """Canvas que conoce el total de páginas para poner 'Página X de Y'."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._paginas = []

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self._pie(total)
            super().showPage()
        super().save()

    def _pie(self, total):
        self.setStrokeColor(LINEA)
        self.setLineWidth(0.6)
        self.line(MARGEN, 12 * mm, ANCHO - MARGEN, 12 * mm)
        self.setFont('Helvetica', 7)
        self.setFillColor(GRIS)
        self.drawString(MARGEN, 8 * mm, f'Iurko Chacinados · generado el {self._generado}')
        self.drawRightString(ANCHO - MARGEN, 8 * mm, f'Página {self._pageNumber} de {total}')


def _encabezado(titulo, subtitulo):
    def dibujar(c, doc):
        c.saveState()
        c.setFillColor(BORDO_OSC)
        c.rect(0, ALTO - 26 * mm, ANCHO, 26 * mm, stroke=0, fill=1)
        c.setFillColor(CUERO)
        c.rect(0, ALTO - 27.2 * mm, ANCHO, 1.2 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont('Times-Bold', 17)
        c.drawString(MARGEN, ALTO - 12.5 * mm, 'Iurko Chacinados')
        c.setFont('Helvetica', 7.2)
        c.setFillColor(colors.HexColor('#e9cfc6'))
        c.drawString(MARGEN, ALTO - 17.5 * mm, 'DESPENSA ARTESANAL · MISIONES, ARGENTINA')
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 11)
        c.drawRightString(ANCHO - MARGEN, ALTO - 12 * mm, titulo)
        c.setFont('Helvetica', 8.5)
        c.setFillColor(colors.HexColor('#e9cfc6'))
        c.drawRightString(ANCHO - MARGEN, ALTO - 17.5 * mm, subtitulo)
        c.restoreState()
    return dibujar


def _documento(titulo, subtitulo, historia, nombre_archivo):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGEN, rightMargin=MARGEN,
                            topMargin=33 * mm, bottomMargin=18 * mm,
                            title=f'{titulo} · {subtitulo}', author='Iurko Chacinados', subject=titulo)
    generado = timezone.localtime().strftime('%d/%m/%Y %H:%M')

    def lienzo(*a, **k):
        c = _Lienzo(*a, **k)
        c._generado = generado
        return c

    dibujar = _encabezado(titulo, subtitulo)
    doc.build(historia, onFirstPage=dibujar, onLaterPages=dibujar, canvasmaker=lienzo)
    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return resp


# --------------------------------------------------------------------------- piezas comunes

def _tabla(encabezado, filas, anchos, num_desde=1, total=None, alinear_izq=()):
    """Tabla con encabezado bordó, filas alternadas, números a la derecha y fila de total opcional."""
    def celda(v, j, negrita=False):
        if isinstance(v, Paragraph):
            return v
        estilo = 'celda_b' if negrita else ('celda_num' if j >= num_desde and j not in alinear_izq else 'celda')
        if negrita and j >= num_desde and j not in alinear_izq:
            estilo = ParagraphStyle('tb', parent=E['celda_num'], fontName='Helvetica-Bold')
        return Paragraph(esc(v), E[estilo] if isinstance(estilo, str) else estilo)

    cab = [Paragraph(f'<font color="white"><b>{esc(h)}</b></font>',
                     ParagraphStyle('cab', parent=E['celda'], fontSize=7.2,
                                    alignment=TA_RIGHT if (j >= num_desde and j not in alinear_izq) else 0))
           for j, h in enumerate(encabezado)]
    datos = [cab] + [[celda(v, j) for j, v in enumerate(f)] for f in filas]
    if total:
        datos.append([celda(v, j, negrita=True) for j, v in enumerate(total)])
    factor = UTIL / sum(anchos)          # que la tabla ocupe justo el ancho útil
    t = Table(datos, colWidths=[a * factor for a in anchos], repeatRows=1)
    estilo = [
        ('BACKGROUND', (0, 0), (-1, 0), BORDO),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINEA),
    ]
    for i in range(1, len(filas) + 1):
        if i % 2 == 0:
            estilo.append(('BACKGROUND', (0, i), (-1, i), FONDO))
    if total:
        estilo += [('LINEABOVE', (0, -1), (-1, -1), 1, BORDO), ('BACKGROUND', (0, -1), (-1, -1), colors.white)]
    t.setStyle(TableStyle(estilo))
    return t


def _kpis(items):
    """Fila de recuadros de resumen: [(título, valor, detalle), ...]."""
    ancho = UTIL / len(items)
    celdas = [[Paragraph(esc(t).upper(), E['kpi_t']), Paragraph(esc(v), E['kpi_v']), Paragraph(esc(s), E['kpi_s'])]
              for t, v, s in items]
    t = Table([celdas], colWidths=[ancho] * len(items))
    t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, LINEA), ('INNERGRID', (0, 0), (-1, -1), 0.6, LINEA),
        ('BACKGROUND', (0, 0), (-1, -1), FONDO), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))
    return t


def _grafico_barras(titulo, etiquetas, series, formato, ancho, alto=58 * mm):
    """Gráfico de barras verticales. series = [(nombre, color, valores)]. Un solo eje."""
    d = Drawing(ancho, alto)
    d.add(String(0, alto - 10, titulo, fontName='Times-Bold', fontSize=10.5, fillColor=BORDO_OSC))
    bc = VerticalBarChart()
    bc.x, bc.y = 34, 22
    bc.width, bc.height = ancho - 44, alto - 50
    bc.data = [[float(v) for v in vals] for _, _, vals in series]
    bc.strokeColor = None
    bc.barSpacing = 1.5
    bc.groupSpacing = 8 if len(etiquetas) < 8 else 4
    for i, (_, color, _) in enumerate(series):
        bc.bars[i].fillColor = color
        bc.bars[i].strokeColor = None
    tope = max([max(s, default=0) for s in bc.data] + [1])
    bc.valueAxis.valueMin = 0
    bc.valueAxis.valueMax = tope * 1.12
    bc.valueAxis.labels.fontName = 'Helvetica'
    bc.valueAxis.labels.fontSize = 6.5
    bc.valueAxis.labels.fillColor = GRIS
    bc.valueAxis.labelTextFormat = formato
    bc.valueAxis.strokeColor = LINEA
    bc.valueAxis.gridStrokeColor = LINEA
    bc.valueAxis.gridStrokeWidth = 0.4
    bc.valueAxis.visibleGrid = True
    bc.valueAxis.maximumTicks = 5
    bc.categoryAxis.categoryNames = etiquetas
    bc.categoryAxis.labels.fontName = 'Helvetica'
    bc.categoryAxis.labels.fontSize = 6.5
    bc.categoryAxis.labels.fillColor = GRIS
    bc.categoryAxis.strokeColor = LINEA
    bc.categoryAxis.tickDown = 0
    if len(etiquetas) > 8:
        bc.categoryAxis.labels.angle = 35
        bc.categoryAxis.labels.dy = -6
        bc.categoryAxis.labels.dx = -6
    d.add(bc)
    if len(series) > 1:
        lg = Legend()
        lg.x, lg.y = ancho - 4, alto - 6
        lg.alignment = 'right'
        lg.boxAnchor = 'ne'
        lg.columnMaximum = 1
        lg.deltax = 60
        lg.dxTextSpace = 4
        lg.fontName = 'Helvetica'
        lg.fontSize = 7
        lg.fillColor = GRIS
        lg.strokeColor = None
        lg.colorNamePairs = [(c, n) for n, c, _ in series]
        lg.dx = lg.dy = 6
        d.add(lg)
    return d


def _fmt_pesos_eje(v):
    return pesos(v) if v < 1_000_000 else f'$ {num(Decimal(v) / 1_000_000, 1)} M'


def _fmt_kg_eje(v):
    return num(v, 0)


# --------------------------------------------------------------------------- reporte de producción y ventas

SECCIONES = {
    'completo': 'Reporte de producción y ventas',
    'recetas': 'Producción y ventas por receta',
    'meses': 'Producción y ventas mes a mes',
    'clientes': 'Mejores clientes',
    'insumos': 'Materias primas usadas',
    'fuera': 'Ventas por fuera de la web',
}


def reporte(seccion, datos, desde, hasta):
    titulo = SECCIONES.get(seccion, SECCIONES['completo'])
    periodo = f'Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
    tot = datos['tot']
    h = []

    if seccion == 'completo':
        h.append(_kpis([
            ('Ventas', pesos(tot['ventas']),
             f"{pl(tot['pedidos'], 'pedido')} web · {pesos(tot['fuera_ventas'])} por fuera" if tot['fuera_piezas']
             else f"{pl(tot['pedidos'], 'pedido')} · ticket {pesos(tot['ticket'])}"),
            ('Ganancia estimada', pesos(tot['ganancia']),
             f"{tot['margen']} % sobre lo vendido" if tot['margen'] is not None else 'sin ventas'),
            ('Kilos vendidos', kg(tot['kg_vend']), f"{pl(tot['piezas_vend'], 'pieza')} · {pl(tot['clientes'], 'cliente')}"),
            ('Kilos producidos', kg(tot['kg_prod']), f"insumos {pesos(tot['costo_prod'])}"),
            ('Mermas', kg(tot['kg_baja']), 'piezas dadas de baja'),
        ]))
        h.append(Spacer(1, 4 * mm))
        meses = datos['meses']
        if len(meses) > 1:
            etiquetas = [m['etiqueta'] for m in meses]
            mitad = (UTIL - 6 * mm) / 2
            g1 = _grafico_barras('Ventas por mes', etiquetas, [('Ventas', SERIE_VEND, [m['ventas'] for m in meses])],
                                 _fmt_pesos_eje, mitad)
            g2 = _grafico_barras('Kilos por mes', etiquetas,
                                 [('Producidos', SERIE_PROD, [m['kg_prod'] for m in meses]),
                                  ('Vendidos', SERIE_VEND, [m['kg_vend'] for m in meses])], _fmt_kg_eje, mitad)
            g = Table([[g1, g2]], colWidths=[mitad + 3 * mm, mitad + 3 * mm])
            g.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                                   ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            h.append(g)

    if seccion in ('completo', 'recetas'):
        h.append(Paragraph('Por receta', E['h2']))
        if datos['recetas']:
            filas = [[f['receta'].nombre, kg(f['kg_prod']), kg(f['kg_vend']), f['piezas_vend'], pesos(f['ventas']),
                      pesos(f['costo_vend']) + (' *' if f['sin_costo'] else ''), pesos(f['ganancia']),
                      f"{f['margen']} %" if f['margen'] is not None else '-',
                      kg(f['kg_baja']) if f['kg_baja'] else '-'] for f in datos['recetas']]
            total = ['Total', kg(tot['kg_prod']), kg(tot['kg_vend']), tot['piezas_vend'], pesos(tot['ventas']),
                     pesos(tot['costo_vend']), pesos(tot['ganancia']),
                     f"{tot['margen']} %" if tot['margen'] is not None else '', kg(tot['kg_baja'])]
            anchos = [38 * mm, 18 * mm, 17 * mm, 13 * mm, 21 * mm, 21 * mm, 21 * mm, 14 * mm, 17 * mm]
            h.append(_tabla(['Receta', 'Producido', 'Vendido', 'Piezas', 'Ventas', 'Costo vendido', 'Ganancia',
                             'Margen', 'Mermas'], filas, anchos, total=total))
            h.append(Spacer(1, 2 * mm))
            nota = ('Ganancia estimada = ventas - costo de las materias primas de las piezas vendidas (según el costo '
                    'por kg de su tanda). No incluye luz, gas, envases ni mano de obra.')
            if tot['fuera_piezas']:
                nota += (f" Incluye {pl(tot['fuera_piezas'], 'pieza')} vendida{'s' if tot['fuera_piezas'] != 1 else ''} "
                         f"por fuera de la web (WhatsApp, local, ferias) por {pesos(tot['fuera_ventas'])}.")
            if tot['sin_costo']:
                nota += ' * Hay piezas de tandas cargadas sin descontar materias primas: su costo no se conoce.'
            h.append(Paragraph(nota, E['nota']))
        else:
            h.append(Paragraph('No hubo producción ni ventas en el período.', E['p']))

    if seccion in ('completo', 'meses'):
        bloque = [Paragraph('Mes a mes', E['h2'])]
        filas = [[m['etiqueta'].capitalize(), m['pedidos'], pesos(m['ventas']), kg(m['kg_vend']), kg(m['kg_prod'])]
                 for m in datos['meses']]
        total = ['Total', tot['pedidos'], pesos(tot['ventas']), kg(tot['kg_vend']), kg(tot['kg_prod'])]
        bloque.append(_tabla(['Mes', 'Pedidos', 'Ventas', 'Kg vendidos', 'Kg producidos'], filas,
                             [40 * mm, 25 * mm, 40 * mm, 37 * mm, 38 * mm], total=total))
        h.append(KeepTogether(bloque) if len(filas) < 14 else bloque[0])
        if len(filas) >= 14:
            h.append(bloque[1])

    if seccion in ('completo', 'clientes'):
        bloque = [Paragraph('Mejores clientes', E['h2'])]
        if datos['clientes']:
            filas = [[c['nombre'], c['cliente__email'], c['n'], pesos(c['plata'])] for c in datos['clientes']]
            bloque.append(_tabla(['Cliente', 'Correo', 'Pedidos', 'Total'], filas,
                                 [55 * mm, 70 * mm, 22 * mm, 33 * mm], num_desde=2))
        else:
            bloque.append(Paragraph('Sin ventas en el período.', E['p']))
        h.append(KeepTogether(bloque))

    if seccion in ('completo', 'insumos'):
        bloque = [Paragraph('Materias primas usadas', E['h2'])]
        if datos['insumos']:
            filas = [[i['lote__tipo__nombre'], f"{num(i['cant'], 3)} {i['lote__tipo__unidad__abreviatura']}",
                      pesos(i['costo'])] for i in datos['insumos']]
            total = ['Total', '', pesos(sum((i['costo'] or 0 for i in datos['insumos']), Decimal(0)))]
            bloque.append(_tabla(['Materia prima', 'Cantidad usada', 'Costo'], filas,
                                 [90 * mm, 45 * mm, 45 * mm], total=total))
            bloque.append(Spacer(1, 2 * mm))
            bloque.append(Paragraph('Lo que se descontó del depósito al cargar las tandas del período.', E['nota']))
        else:
            bloque.append(Paragraph('No se usaron materias primas en el período.', E['p']))
        h.append(KeepTogether(bloque))

    fuera = datos['fuera']
    if seccion == 'fuera' or (seccion == 'completo' and fuera['piezas']):
        _seccion_fuera(h, fuera, solo=seccion == 'fuera')

    h.append(Spacer(1, 5 * mm))
    if seccion == 'fuera':
        h.append(Paragraph('Ventas registradas a mano en cada tanda (WhatsApp, en el local, ferias, por encargo), '
                           'contadas por la fecha de venta que se anotó. El importe es el total cobrado que se cargó '
                           'o, si no se cargó, el peso de cada pieza por el precio por kg de la receta.', E['nota']))
    else:
        h.append(Paragraph('Ventas web contadas por fecha de pago, sin pedidos cancelados; ventas por fuera de la web '
                           'por la fecha de venta anotada. Producción contada por fecha de producción; las tandas '
                           'anuladas no suman como producción.', E['nota']))
    nombre = f'iurko-{seccion}-{desde:%Y%m%d}-{hasta:%Y%m%d}.pdf'
    return _documento(titulo, periodo, h, nombre)


def _seccion_fuera(h, fuera, solo=False):
    if solo:
        h.append(_kpis([
            ('Vendido por fuera', pesos(fuera['total']), f"{fuera['pct_ventas']} % de las ventas del período"),
            ('Piezas', str(fuera['piezas']), 'registradas a mano'),
            ('Kilos', kg(fuera['kg']), f"promedio {pesos(fuera['promedio_kg'])} por kg"),
            ('Canales', str(len(fuera['canales'])), ', '.join(c['nombre'] for c in fuera['canales'][:3]) or '-'),
        ]))
        h.append(Spacer(1, 4 * mm))
    cabecera = [] if solo else [
        Paragraph('Ventas por fuera de la web', E['h2']),
        Paragraph(f"{pl(fuera['piezas'], 'pieza')} · {kg(fuera['kg'])} · {pesos(fuera['total'])} "
                  f"({fuera['pct_ventas']} % de las ventas del período).", E['p'])]
    if not fuera['piezas']:
        h += cabecera
        h.append(Paragraph('No se registraron ventas por fuera de la web en el período.', E['p']))
        return
    anchos = [70 * mm, 25 * mm, 30 * mm, 35 * mm, 20 * mm]
    for titulo, filas in (('Por canal', fuera['canales']), ('Por receta', fuera['recetas'])):
        bloque, cabecera = cabecera + [Paragraph(titulo, E['h2'] if solo else E['h3'])], []
        bloque.append(_tabla([titulo.replace('Por ', '').capitalize(), 'Piezas', 'Kilos', 'Total', '%'],
                             [[f['nombre'], f['piezas'], kg(f['kg']), pesos(f['total']), f"{f['pct']} %"]
                              for f in filas], anchos,
                             total=['Total', fuera['piezas'], kg(fuera['kg']), pesos(fuera['total']), '100 %']))
        h.append(KeepTogether(bloque))
    h.append(Paragraph('Detalle de las ventas', E['h2'] if solo else E['h3']))
    filas = [[f"{d['fecha']:%d/%m/%Y}", d['canal'], Paragraph(esc(d['cliente'] or '-'), E['celda']),
              Paragraph(esc(d['receta']), E['celda']),
              Paragraph(f'<font name="Courier" size="7">{d["codigo"]}</font>', E['celda']),
              f"{d['peso_g']} g", pesos(d['monto'])] for d in fuera['detalle']]
    h.append(_tabla(['Fecha', 'Canal', 'Cliente', 'Receta', 'Pieza', 'Peso', 'Importe'], filas,
                    [19 * mm, 32 * mm, 29 * mm, 33 * mm, 31 * mm, 15 * mm, 21 * mm], num_desde=5,
                    total=['Total', '', '', '', '', kg(fuera['kg']), pesos(fuera['total'])]))


# --------------------------------------------------------------------------- trazabilidad

def _tabla_clientes(pedidos, resaltar=''):
    if not pedidos:
        return Paragraph('Todavía no se vendió ni reservó ninguna pieza.', E['p'])
    filas = []
    for f in pedidos:
        p, u = f['pedido'], f['usuario']
        contacto = '<br/>'.join(esc(x) for x in [u.email, f['telefono']] if x)
        fecha = (f"pagado {p.pagado_en:%d/%m/%Y}" if p.pagado_en else f"{p.creado:%d/%m/%Y} · sin pagar")
        codigos = ', '.join((f'<b>{c}</b>' if c == resaltar else c) for c in (x.codigo for x in f['piezas']))
        filas.append([
            Paragraph(f"<b>{esc(nombre_cliente(u))}</b>" + (f"<br/>{esc(f['localidad'])}" if f['localidad'] else ''),
                      E['celda']),
            Paragraph(contacto or '-', E['celda']),
            Paragraph(f"#{p.numero} · {esc(p.get_estado_display())}<br/>{fecha}", E['celda']),
            Paragraph(f'<font name="Courier" size="7">{codigos}</font>', E['celda']),
        ])
    return _tabla(['Cliente', 'Contacto', 'Pedido', 'Piezas'], filas, [38 * mm, 45 * mm, 33 * mm, 64 * mm],
                  num_desde=99)


def _tabla_fuera(fuera, resaltar=''):
    filas = []
    for p in fuera:
        detalle = (p.motivo_baja or '').replace('Venta por fuera de la web · ', '')
        cod = f'<b>{p.codigo}</b>' if p.codigo == resaltar else p.codigo
        filas.append([Paragraph(esc(detalle) or '-', E['celda']), f'{p.venta_externa_fecha:%d/%m/%Y}',
                      Paragraph(f'<font name="Courier" size="7">{cod}</font>', E['celda']),
                      f'{p.peso_g} g', pesos(p.venta_externa_monto) if p.venta_externa_monto is not None else '-'])
    return _tabla(['Canal / cliente', 'Fecha', 'Pieza', 'Peso', 'Importe'], filas,
                  [62 * mm, 22 * mm, 46 * mm, 22 * mm, 28 * mm], num_desde=3)


def _bloque_fuera(h, fuera, resaltar=''):
    if fuera:
        h.append(Paragraph('Vendidas por fuera de la web', E['h2']))
        h.append(Paragraph('Ventas registradas a mano (WhatsApp, en el local, ferias): no tienen pedido en la tienda; '
                           'el canal y el cliente son los que se anotaron al registrarlas.', E['nota']))
        h.append(_tabla_fuera(fuera, resaltar))


def traza_lote(lote, tandas, pedidos, personas, fuera=()):
    h = [
        _kpis([
            ('Materia prima', lote.tipo.nombre, f'{lote.compra.proveedor} · {lote.compra.fecha:%d/%m/%Y}'),
            ('Comprado', f'{num(lote.cantidad, 2)} {lote.tipo.unidad}',
             f'lote del proveedor {lote.lote}' if lote.lote else 'sin n.º de lote del proveedor'),
            ('Tandas que lo usaron', str(len(tandas)), ''),
            ('Clientes alcanzados', str(len(personas)), pl(len(pedidos), 'pedido')),
        ]),
        Paragraph('Tandas que usaron este lote', E['h2']),
    ]
    if tandas:
        filas = [[t.codigo, t.receta.nombre, f'{t.fecha_produccion:%d/%m/%Y}', f'{num(t.usado, 3)} {lote.tipo.unidad}',
                  t.resumen['vendidas'], t.resumen['libres'], t.etiqueta_estado()[0]] for t in tandas]
        h.append(_tabla(['Tanda', 'Receta', 'Producción', 'Usado', 'Vendidas', 'Libres', 'Estado'], filas,
                        [22 * mm, 42 * mm, 21 * mm, 23 * mm, 16 * mm, 14 * mm, 42 * mm], num_desde=3,
                        alinear_izq=(6,)))
    else:
        h.append(Paragraph('Este lote todavía no se usó en ninguna tanda.', E['p']))
    h.append(Paragraph('A quién llegó', E['h2']))
    h.append(_tabla_clientes(pedidos))
    _bloque_fuera(h, fuera)
    sub = f'{lote.tipo.nombre} · {lote.compra.proveedor} · {lote.compra.fecha:%d/%m/%Y}'
    return _documento('Informe de trazabilidad', sub, h, f'iurko-trazabilidad-lote-{lote.pk}.pdf')


def traza_tanda(tanda, consumos, pedidos, personas, resumen, pieza=None, fuera=()):
    h = []
    if pieza:
        venta = getattr(pieza, 'venta', None)
        txt = (f'Pieza <b>{pieza.codigo}</b> · {pieza.peso_g} g · {esc(pieza.get_estado_display())}')
        if venta:
            txt += f' · pedido #{venta.item.pedido.numero} de <b>{esc(nombre_cliente(venta.item.pedido.cliente))}</b>'
        elif pieza.vendida_fuera:
            txt += f' · {esc(pieza.motivo_baja)} · {pieza.venta_externa_fecha:%d/%m/%Y}'
        h += [Paragraph(txt, ParagraphStyle('pz', parent=E['p'], backColor=colors.HexColor('#fbefd6'),
                                            borderColor=colors.HexColor('#c98a12'), borderWidth=0.8,
                                            borderPadding=6, spaceAfter=8)), Spacer(1, 2 * mm)]
    h.append(_kpis([
        ('Receta', tanda.receta.nombre, f'tanda {tanda.codigo}'),
        ('Producida', f'{tanda.fecha_produccion:%d/%m/%Y}',
         f'consumir antes del {tanda.fecha_vencimiento:%d/%m/%Y}' if tanda.fecha_vencimiento else 'sin vencimiento'),
        ('Piezas', str(resumen['total']), f"{pl(resumen['vendidas'], 'vendida')} · {pl(resumen['libres'], 'libre')}"
                                          + (f" ({resumen['fuera']} por fuera de la web)" if resumen.get('fuera') else '')
                                          + (f" · {resumen['baja']} de baja" if resumen['baja'] else '')),
        ('Clientes', str(len(personas)), pl(len(pedidos), 'pedido')),
    ]))
    h.append(Paragraph('De dónde salió (materias primas)', E['h2']))
    consumos = list(consumos)
    if consumos:
        filas = [[c.lote.tipo.nombre, str(c.lote.compra.proveedor),
                  f'{c.lote.compra.fecha:%d/%m/%Y}' + (f' · {c.lote.compra.numero_comprobante}'
                                                      if c.lote.compra.numero_comprobante else ''),
                  c.lote.lote or '-', f'{num(c.cantidad, 3)} {c.lote.tipo.unidad}'] for c in consumos]
        h.append(_tabla(['Materia prima', 'Proveedor', 'Compra', 'Lote prov.', 'Usado'], filas,
                        [38 * mm, 45 * mm, 45 * mm, 24 * mm, 28 * mm], num_desde=4))
    else:
        h.append(Paragraph('No hay materias primas registradas para esta tanda.', E['p']))
    h.append(Paragraph('A quién llegó', E['h2']))
    h.append(_tabla_clientes(pedidos, resaltar=pieza.codigo if pieza else ''))
    _bloque_fuera(h, fuera, resaltar=pieza.codigo if pieza else '')
    nombre = f'iurko-trazabilidad-{pieza.codigo if pieza else tanda.codigo}.pdf'
    return _documento('Informe de trazabilidad', f'{tanda.receta.nombre} · tanda {tanda.codigo}', h, nombre)
