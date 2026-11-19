# Iurko Chacinados — ERP y tienda web

Sistema de producción e inventario por tandas (FIFO) y tienda online con pago por QR de Mercado Pago.
Hecho con Django + SQLite. Funciona con **Python 3.10 o más nuevo**:
con Python 3.10/3.11 se instala Django 5.2 y con Python 3.12+ Django 6.1 (el `requirements.txt` elige solo).

## Cómo arrancarlo

```bash
python -m venv Final-env
Final-env\Scripts\activate          # en Windows  (Linux/Mac: source Final-env/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py cargar_demo        # opcional: datos de ejemplo
python manage.py runserver
```

- Tienda: http://127.0.0.1:8000/
- Panel del productor: http://127.0.0.1:8000/panel/
- Usuarios de ejemplo (con `cargar_demo`), todos con clave **Iurko2026!**:
  - **productor**: entra al panel de gestión.
  - **cliente**, **martin** y **sofia**: clientes de la tienda.
- `cargar_demo` deja ejemplos de todo lo que muestra el sistema: 6 recetas y 7 tandas (una por vencer,
  una pausada, una agotada y otra estacionándose), pedidos en cada estado (pagado, preparando, listo para
  retirar y entregado), ventas por fuera de la web, una baja, un “avisame cuando haya” y una consulta sin
  responder. Solo carga datos si la base no tiene recetas; para empezar de cero, borrar `db.sqlite3` y
  correr de nuevo `migrate` y `cargar_demo`.
- Para crear el productor real: `python manage.py createsuperuser` (queda con acceso al panel).

## Cómo funciona

### Códigos de tandas y piezas (solo números)
| Qué | Formato | Ejemplo |
|---|---|---|
| Tanda | `DDMMAAAA` + `TT` (n.º de tanda de ese día, 01–99) | `0511202601` |
| Pieza | `DDMMAAAA` + `TT` + `NNNN` (n.º de pieza) | `05112026010021` |

Ej.: el 05/11/2026 se hacen salame (tanda `01`) y bondiola (tanda `02`).
El orden FIFO se calcula con la **fecha real**, no con el texto del código
(como texto, `01122026…` quedaría antes que `05112026…`).

### Producción (panel)
1. **Cargar compra**: fecha, vendedor, ticket o factura (PDF/foto, privado) y qué se compró.
2. **Recetas**: ingredientes para X kg, precio por kg, peso aproximado por pieza y días de estacionamiento.
3. **Cargar tanda**: receta + kg + cantidad de piezas. Se descuentan los ingredientes de las compras
   más viejas primero y se generan los códigos de las piezas. Después se puede cargar el peso real de cada pieza.
4. **Inventario**: stock por tanda en orden FIFO. Una tanda se puede **pausar** (no se vende ninguna pieza
   hasta reanudarla; los QR en curso con piezas de esa tanda se cancelan) o **anular** (se descartan las piezas).

### Tienda (clientes)
- El cliente compra **por piezas** y paga el **peso real** × precio por kg.
- Al tocar **Pagar con Mercado Pago** se **reservan las piezas por 15 minutos** (las más viejas, FIFO)
  y se genera el QR con el monto exacto. Si paga, quedan vendidas; si el QR vence, vuelven al stock.
- Si un producto no tiene stock: **"Avisame cuando haya"**. No se cobra nada por adelantado: cuando sale
  una tanda nueva se avisa por correo y en la web, con el precio de ese momento.

### Tareas periódicas
La web libera reservas vencidas y manda avisos sola cuando alguien entra. Para que no dependa de eso,
programá cada 5 minutos (Programador de tareas de Windows o cron):
```bash
python manage.py tareas_tienda
```

## Mercado Pago (cobro real)
Sin token, todo funciona en **modo demo**: el QR no cobra y aparece un botón "Simular pago aprobado".
Para cobrar de verdad (API de Orders, QR dinámico):
1. Crear una aplicación en https://www.mercadopago.com.ar/developers/panel/app y copiar el **Access Token**.
2. Crear una **sucursal y una caja** (docs: QR Code > Crear sucursal y caja) y anotar el `external_pos_id`.
3. Copiar `ejemplo_env.txt` con el nombre `.env` y completar `MP_ACCESS_TOKEN` y `MP_EXTERNAL_POS_ID`.
4. (Producción) En *Tus integraciones > Webhooks* suscribirse al tópico **Order** con la URL
   `https://TU-DOMINIO/mp/webhook/` y copiar la clave secreta en `MP_WEBHOOK_SECRET`.
   Aunque no lleguen los webhooks (por ejemplo en localhost), la pantalla de pago consulta el estado cada 4 segundos.

## Correo (recuperar contraseña y formulario de contacto)

Sin configurar nada, los correos se imprimen en la consola donde corre `runserver` (sirve para probar).
Para que salgan de verdad con Gmail:

1. En la cuenta de Gmail de Iurko activar la verificación en 2 pasos.
2. Entrar a https://myaccount.google.com/apppasswords y crear una "contraseña de aplicación" (16 letras).
3. En el `.env` completar `EMAIL_USER=cuenta@gmail.com` y `EMAIL_PASSWORD=` con esas 16 letras (sin espacios).
4. Reiniciar el servidor y en el panel → **Consultas** tocar "Enviar correo de prueba".

Las consultas llegan al correo cargado en Página "Nosotros" → Correo, y además quedan guardadas en el panel.

## Estructura
```
ERP/          configuración (settings, urls)
users/        usuario, roles, login y registro
produccion/   materias primas, compras, recetas, tandas, piezas y su lógica (services.py)
tienda/       clientes, carrito, pedidos, reservas, avisos de stock y Mercado Pago
panel/        pantallas del productor
templates/    HTML de la tienda y del panel
static/       css/iurko.css (diseño "Artesanía & Terruño") y js/iurko.js
```

## Tests
```bash
python manage.py test
```
