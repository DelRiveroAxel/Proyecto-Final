/* Iurko Chacinados — interacciones de la tienda y del panel (sin dependencias). */
(function () {
  'use strict';
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  // ---------- Menú lateral en celular ----------
  $$('[data-menu]').forEach(function (b) {
    b.addEventListener('click', function () {
      document.body.classList.toggle('menu-open');
      var w = $('.ad-wrap'); if (w) w.classList.toggle('menu-open');
    });
  });

  // ---------- Tema claro / oscuro ----------
  $$('[data-tema]').forEach(function (b) {
    b.addEventListener('click', function () {
      var d = document.documentElement, oscuro = d.getAttribute('data-theme') === 'dark';
      if (oscuro) d.removeAttribute('data-theme'); else d.setAttribute('data-theme', 'dark');
      try { localStorage.setItem('ik_tema', oscuro ? 'light' : 'dark'); } catch (e) {}
    });
  });

  // ---------- Selector de cantidad [-] n [+] ----------
  $$('[data-qty-form]').forEach(function (form) {
    var input = $('input[name=cantidad]', form);
    if (!input) return;
    var auto = form.hasAttribute('data-autosubmit'), timer;
    function fijar(v) {
      var min = parseInt(input.min || '0', 10), max = parseInt(input.max || '99', 10);
      if (isNaN(v)) v = min;
      v = Math.max(min, Math.min(max || 99, v));
      input.value = v;
      $$('[data-qty]', form).forEach(function (b) {
        var d = parseInt(b.getAttribute('data-qty'), 10);
        b.disabled = (d < 0 && v <= min) || (d > 0 && max && v >= max);
      });
      if (auto) { clearTimeout(timer); timer = setTimeout(function () { form.submit(); }, 450); }
    }
    $$('[data-qty]', form).forEach(function (b) {
      b.addEventListener('click', function () { fijar(parseInt(input.value, 10) + parseInt(b.getAttribute('data-qty'), 10)); });
    });
    input.addEventListener('change', function () { fijar(parseInt(input.value, 10)); });
  });

  // ---------- Carrusel del inicio ----------
  var hero = $('[data-hero]');
  if (hero) {
    var slides = $$('.ik-hero-slide', hero), dots = $$('[data-hero-dot]', hero), i = 0, t;
    var ir = function (n) {
      i = (n + slides.length) % slides.length;
      slides.forEach(function (s, k) { s.classList.toggle('on', k === i); });
      dots.forEach(function (d, k) { d.classList.toggle('on', k === i); });
    };
    var auto = function () { clearInterval(t); if (slides.length > 1) t = setInterval(function () { ir(i + 1); }, 6000); };
    var prev = $('[data-hero-prev]', hero), next = $('[data-hero-next]', hero);
    if (prev) prev.addEventListener('click', function () { ir(i - 1); auto(); });
    if (next) next.addEventListener('click', function () { ir(i + 1); auto(); });
    dots.forEach(function (d) { d.addEventListener('click', function () { ir(+d.getAttribute('data-hero-dot')); auto(); }); });
    auto();
  }

  // ---------- Galería de fotos del producto: flechas, deslizar y zoom ----------
  var lb = document.getElementById('ik-lightbox');
  $$('[data-galeria]').forEach(function (g) {
    var principal = $('[data-galeria-principal]', g);
    var botones = $$('[data-galeria-foto]', g);
    var fotos = botones.map(function (b) { return b.getAttribute('data-galeria-foto'); });
    var cont = $('[data-gal-cont]', g), i = 0;
    if (!principal || !fotos.length) return;
    var mostrar = function (n) {
      i = (n + fotos.length) % fotos.length;
      principal.src = fotos[i];
      botones.forEach(function (b, k) { b.classList.toggle('on', k === i); });
      if (cont) cont.textContent = (i + 1) + ' / ' + fotos.length;
      var activa = botones[i]; if (activa && activa.scrollIntoView && activa.offsetParent) activa.scrollIntoView({block: 'nearest', inline: 'nearest'});
    };
    botones.forEach(function (b, k) { b.addEventListener('click', function () { mostrar(k); }); });
    var prev = $('[data-gal-prev]', g), next = $('[data-gal-next]', g);
    if (prev) prev.addEventListener('click', function (e) { e.stopPropagation(); mostrar(i - 1); });
    if (next) next.addEventListener('click', function (e) { e.stopPropagation(); mostrar(i + 1); });
    // Deslizar con el dedo
    var x0 = null;
    principal.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; }, {passive: true});
    principal.addEventListener('touchend', function (e) {
      if (x0 === null) return;
      var dx = e.changedTouches[0].clientX - x0; x0 = null;
      if (Math.abs(dx) > 40 && fotos.length > 1) mostrar(dx < 0 ? i + 1 : i - 1);
    });
    var abrir = function () { if (lb) abrirLightbox(fotos, i, mostrar); };
    principal.addEventListener('click', abrir);
    var lupa = $('[data-gal-abrir]', g); if (lupa) lupa.addEventListener('click', abrir);
  });

  function abrirLightbox(fotos, inicio, alCambiar) {
    var img = $('[data-lb-img]', lb), escena = $('[data-lb-escena]', lb), cont = $('[data-lb-cont]', lb);
    var i = inicio, z = 1, tx = 0, ty = 0, arrastrando = false, movio = false, px = 0, py = 0;
    var aplicar = function () {
      img.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + z + ')';
      escena.classList.toggle('con-zoom', z > 1);
    };
    var reset = function () { z = 1; tx = 0; ty = 0; aplicar(); };
    var mostrar = function (n) {
      i = (n + fotos.length) % fotos.length;
      img.src = fotos[i]; reset();
      cont.textContent = fotos.length > 1 ? (i + 1) + ' / ' + fotos.length : '';
      $$('.ik-lb-flecha', lb).forEach(function (b) { b.hidden = fotos.length < 2; });
      if (alCambiar) alCambiar(i);
    };
    var zoom = function (nuevo, cx, cy) {
      nuevo = Math.max(1, Math.min(5, nuevo));
      var r = escena.getBoundingClientRect();
      cx = cx === undefined ? r.width / 2 : cx - r.left; cy = cy === undefined ? r.height / 2 : cy - r.top;
      // mantiene quieto el punto que está bajo el cursor
      var ox = (cx - r.width / 2 - tx) / z, oy = (cy - r.height / 2 - ty) / z;
      z = nuevo; tx = cx - r.width / 2 - ox * z; ty = cy - r.height / 2 - oy * z;
      if (z === 1) { tx = 0; ty = 0; }
      aplicar();
    };
    var cerrar = function () { lb.hidden = true; document.body.style.overflow = ''; limpiar(); };
    var tecla = function (e) {
      if (e.key === 'Escape') cerrar();
      else if (e.key === 'ArrowRight') mostrar(i + 1);
      else if (e.key === 'ArrowLeft') mostrar(i - 1);
      else if (e.key === '+') zoom(z * 1.4);
      else if (e.key === '-') zoom(z / 1.4);
    };
    var h = {
      prev: function () { mostrar(i - 1); }, next: function () { mostrar(i + 1); },
      mas: function () { zoom(z * 1.4); }, menos: function () { zoom(z / 1.4); }, cerrar: cerrar,
      fondo: function (e) { if (e.target === lb || e.target === escena) cerrar(); },
      rueda: function (e) { e.preventDefault(); zoom(z * (e.deltaY < 0 ? 1.2 : 1 / 1.2), e.clientX, e.clientY); },
      abajo: function (e) { arrastrando = true; movio = false; px = e.clientX; py = e.clientY; img.style.transition = 'none'; if (img.setPointerCapture) img.setPointerCapture(e.pointerId); },
      mover: function (e) {
        if (!arrastrando) return;
        var dx = e.clientX - px, dy = e.clientY - py;
        if (Math.abs(dx) + Math.abs(dy) > 3) movio = true;
        if (z > 1) { tx += dx; ty += dy; aplicar(); }
        px = e.clientX; py = e.clientY;
      },
      arriba: function (e) {
        if (!arrastrando) return;
        arrastrando = false;
        img.style.transition = '';
        if (!movio) zoom(z > 1 ? 1 : 2.5, e.clientX, e.clientY);          // tocar = acercar / alejar
      }
    };
    // deslizar sin zoom para cambiar de foto (celular)
    var sx = null;
    h.tIni = function (e) { if (z === 1 && e.touches.length === 1) sx = e.touches[0].clientX; };
    h.tFin = function (e) { if (sx === null) return; var dx = e.changedTouches[0].clientX - sx; sx = null; if (z === 1 && Math.abs(dx) > 50) mostrar(dx < 0 ? i + 1 : i - 1); };
    var pares = [[$('[data-lb-prev]', lb), 'click', h.prev], [$('[data-lb-next]', lb), 'click', h.next],
      [$('[data-lb-mas]', lb), 'click', h.mas], [$('[data-lb-menos]', lb), 'click', h.menos],
      [$('[data-lb-cerrar]', lb), 'click', h.cerrar], [lb, 'click', h.fondo], [escena, 'wheel', h.rueda],
      [img, 'pointerdown', h.abajo], [img, 'pointermove', h.mover], [img, 'pointerup', h.arriba],
      [escena, 'touchstart', h.tIni], [escena, 'touchend', h.tFin], [document, 'keydown', tecla]];
    pares.forEach(function (p) { p[0].addEventListener(p[1], p[2], p[1] === 'wheel' ? {passive: false} : undefined); });
    function limpiar() { pares.forEach(function (p) { p[0].removeEventListener(p[1], p[2]); }); }
    lb.hidden = false; document.body.style.overflow = 'hidden';
    mostrar(inicio);
  }

  // ---------- Mostrar / ocultar contraseña ----------
  $$('[data-ver-clave]').forEach(function (b) {
    b.addEventListener('click', function () {
      var inp = document.getElementById(b.getAttribute('data-ver-clave'));
      var ver = inp.type === 'password';
      inp.type = ver ? 'text' : 'password';
      $('.material-symbols-outlined', b).textContent = ver ? 'visibility_off' : 'visibility';
    });
  });

  // ---------- Pantalla de pago: cuenta regresiva + consulta del estado ----------
  var pago = $('#pago');
  if (pago && pago.getAttribute('data-estado') === 'PENDIENTE_PAGO') {
    var seg = parseInt(pago.getAttribute('data-segundos'), 10) || 0, reloj = $('[data-timer]', pago);
    var pintar = function () {
      if (!reloj) return;
      var m = Math.floor(seg / 60), s = seg % 60;
      reloj.textContent = (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
      var qr = $('.ik-qr', pago); if (qr && seg <= 0) qr.classList.add('is-expired');
    };
    pintar();
    setInterval(function () { if (seg > 0) { seg--; pintar(); } }, 1000);
    var consultar = function () {
      fetch(pago.getAttribute('data-estado-url'), { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.estado !== 'PENDIENTE_PAGO') { location.reload(); return; }
          seg = d.segundos; pintar();
          setTimeout(consultar, 4000);
        })
        .catch(function () { setTimeout(consultar, 8000); });
    };
    setTimeout(consultar, 4000);
  }

  // ---------- Panel: nombre del archivo elegido (ticket / factura) ----------
  $$('.ik-file input[type=file]').forEach(function (inp) {
    inp.addEventListener('change', function () {
      var caja = inp.closest('.ik-file'), n = $('.ik-file-name', caja);
      if (!n) { n = document.createElement('span'); n.className = 'ik-file-name'; caja.appendChild(n); }
      n.textContent = inp.files.length > 1 ? inp.files.length + ' fotos elegidas' : (inp.files.length ? inp.files[0].name : '');
    });
  });

  // ---------- Panel: modales (pausar tanda, etc.) ----------
  $$('[data-modal-abrir]').forEach(function (b) {
    b.addEventListener('click', function () { var m = document.getElementById(b.getAttribute('data-modal-abrir')); if (m) m.hidden = false; });
  });
  $$('.ik-modal').forEach(function (m) {
    m.addEventListener('click', function (e) { if (e.target === m || e.target.closest('[data-modal-cerrar]')) m.hidden = true; });
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') $$('.ik-modal').forEach(function (m) { m.hidden = true; }); });

  // ---------- Panel: ingredientes necesarios al cargar una tanda ----------
  var tf = $('[data-tanda-form]');
  if (tf) {
    var rec = $('[name=receta]', tf), kg = $('[name=kg_producidos]', tf), caja = $('[data-necesidad]'), t2;
    var calc = function () {
      if (!rec.value || !kg.value) { caja.innerHTML = '<p class="ik-muted">Elegí la receta y los kg para ver los ingredientes.</p>'; return; }
      fetch(tf.getAttribute('data-necesidad-url') + '?receta=' + rec.value + '&kg=' + encodeURIComponent(kg.value))
        .then(function (r) { return r.text(); }).then(function (h) { caja.innerHTML = h; });
    };
    rec.addEventListener('change', calc);
    kg.addEventListener('input', function () { clearTimeout(t2); t2 = setTimeout(calc, 350); });
    calc();
  }

  // ---------- Panel: renglones dinámicos (ingredientes de receta y renglones de compra) ----------
  // Al elegir algo en el último renglón aparece otro vacío debajo. "Quitar" marca el renglón para borrar.
  $$('[data-formset]').forEach(function (tabla) {
    var prefijo = tabla.getAttribute('data-formset');
    var panel = tabla.closest('.ad-panel') || document;
    var total = document.getElementById('id_' + prefijo + '-TOTAL_FORMS');
    var molde = $('template[data-molde]', panel);
    var cuerpo = $('[data-filas]', tabla);
    var unidades = {};
    try { unidades = JSON.parse((document.getElementById('unidades-json') || {}).textContent || '{}'); } catch (e) {}
    if (!total || !molde || !cuerpo) return;

    var visibles = function () { return $$('[data-fila]', cuerpo).filter(function (f) { return !f.hidden; }); };
    var unidad = function (fila) {
      var sel = $('select', fila), u = $('[data-unidad]', fila);
      if (sel && u) u.textContent = unidades[sel.value] || '';
    };
    var agregar = function (enfocar) {
      var n = parseInt(total.value, 10);
      cuerpo.insertAdjacentHTML('beforeend', molde.innerHTML.replace(/__prefix__/g, n));
      total.value = n + 1;
      var fila = cuerpo.lastElementChild;
      preparar(fila);
      if (enfocar) { var s = $('select', fila); if (s) s.focus(); }
      return fila;
    };
    var asegurarVacia = function () {
      var v = visibles(), ultima = v[v.length - 1];
      if (!ultima || ($('select', ultima) && $('select', ultima).value)) agregar(false);
    };
    var preparar = function (fila) {
      var sel = $('select', fila);
      unidad(fila);
      if (sel) sel.addEventListener('change', function () {
        unidad(fila);
        var v = visibles();
        if (sel.value && v[v.length - 1] === fila) agregar(false);
      });
      var btn = $('[data-quitar]', fila);
      if (btn) btn.addEventListener('click', function () {
        var borrar = $('input[name$="-DELETE"]', fila);
        if (borrar) borrar.checked = true;
        $$('select, input:not([type=hidden]):not([name$="-DELETE"])', fila).forEach(function (c) { c.value = ''; });
        fila.hidden = true;
        asegurarVacia();
      });
    };
    $$('[data-fila]', cuerpo).forEach(preparar);
    $$('[data-agregar]', panel).forEach(function (b) { b.addEventListener('click', function () { agregar(true); }); });
    asegurarVacia();
  });

  // ---------- Panel: días de estacionamiento y de vencimiento según la receta ----------
  var dias = document.getElementById('dias-recetas-json');
  if (dias) {
    var leer = function (id) { var el = document.getElementById(id); try { return el ? JSON.parse(el.textContent || '{}') : {}; } catch (e) { return {}; } };
    var receta = $('[name=receta]'), fechaProd = $('[name=fecha_produccion]');
    var enlazar = function (campo, mapa, aviso, texto) {
      if (!campo) return function () {};
      return function () {
        var d = mapa[receta.value];
        campo.placeholder = (d === undefined || d === null) ? 'Los de la receta' : d + ' (los de la receta)';
        var n = campo.value !== '' ? parseInt(campo.value, 10) : d;
        if (aviso && fechaProd.value) {
          var f = new Date(fechaProd.value + 'T12:00:00'); f.setDate(f.getDate() + (n > 0 ? n : 0));
          aviso.textContent = texto(n, f.toLocaleDateString('es-AR'));
        }
      };
    };
    var pintores = [
      enlazar($('[name=dias_estacionamiento]'), leer('dias-recetas-json'), $('[data-venta-desde]'), function (n, f) {
        return n > 0 ? 'Se va a vender desde el ' + f + '.' : 'Se vende apenas la cargues.'; }),
      enlazar($('[name=dias_vencimiento]'), leer('venc-recetas-json'), $('[data-vence-el]'), function (n, f) {
        return n > 0 ? 'Consumir antes del ' + f + ' (ese día ya no se vende).' : 'Sin vencimiento.'; })
    ];
    var pintarDias = function () { pintores.forEach(function (p) { p(); }); };
    [receta, fechaProd, $('[name=dias_estacionamiento]'), $('[name=dias_vencimiento]')].forEach(function (c) {
      if (c) { c.addEventListener('input', pintarDias); c.addEventListener('change', pintarDias); } });
    pintarDias();
  }

  // ---------- Panel: editor del carrusel con vista previa en vivo ----------
  var sf = $('[data-slide-form]'), prev = $('[data-slide-preview]');
  if (sf && prev) {
    var recetasSlide = {};
    try { recetasSlide = JSON.parse(document.getElementById('recetas-slide-json').textContent); } catch (e) {}
    var campo = function (n) { return sf.querySelector('[name="' + n + '"]'); };
    var P = function (k) { return prev.querySelector('[data-prev-' + k + ']'); };
    var fondoPropio = null;          // dataURL de la foto elegida (todavía sin subir)
    var fondoInicial = (P('fondo').style.backgroundImage || '');
    var quitarFoto = $('[data-slide-quitar-foto]');
    var limites = {titulo: 60, texto: 200, etiqueta: 40};

    var pintar = function () {
      var et = campo('etiqueta').value.trim(), ic = campo('icono').value;
      P('etiqueta').textContent = et;
      P('icono').textContent = ic; P('icono').hidden = !ic;
      P('etiqueta-caja').hidden = !et;
      P('titulo').textContent = campo('titulo').value.trim() || 'Título de la diapositiva';
      var tx = campo('texto').value.trim();
      P('texto').textContent = tx; P('texto').hidden = !tx;
      var bt = campo('boton_texto').value.trim();
      P('boton-texto').textContent = bt; P('boton').hidden = !bt;
      // fondo: foto nueva > foto propia guardada > foto del producto > bordó
      var r = recetasSlide[campo('receta').value];
      var fondo = '';
      if (fondoPropio) fondo = 'url("' + fondoPropio + '")';
      else if (fondoInicial && !(quitarFoto && quitarFoto.checked) && fondoInicial.indexOf('carrusel/') !== -1) fondo = fondoInicial;
      else if (r && r.foto) fondo = 'url("' + r.foto + '")';
      P('fondo').style.backgroundImage = fondo;
      Object.keys(limites).forEach(function (n) {
        var c = sf.querySelector('[data-contador="' + n + '"]');
        if (c) { var l = campo(n).value.length; c.textContent = l + ' / ' + limites[n]; c.style.color = l > limites[n] * 0.9 ? 'var(--urg-rojo)' : ''; }
      });
    };
    $$('input, select, textarea', sf).forEach(function (el) {
      el.addEventListener('input', pintar); el.addEventListener('change', pintar);
    });
    // Completar con los datos del producto
    var completar = function (soloVacios) {
      var r = recetasSlide[campo('receta').value];
      if (!r) return;
      [['titulo', r.titulo], ['texto', r.texto], ['etiqueta', r.etiqueta]].forEach(function (par) {
        var c = campo(par[0]);
        if (!soloVacios || !c.value.trim()) c.value = par[1] || '';
      });
      if (!campo('boton_texto').value.trim()) campo('boton_texto').value = 'Ver producto';
      pintar();
    };
    var bc = $('[data-slide-completar]', sf);
    if (bc) bc.addEventListener('click', function () { completar(false); });
    campo('receta').addEventListener('change', function () { completar(true); });
    // Foto nueva: se ve antes de subirla
    var fi = campo('imagen');
    if (fi) fi.addEventListener('change', function () {
      if (!fi.files.length) { fondoPropio = null; pintar(); return; }
      var lector = new FileReader();
      lector.onload = function () { fondoPropio = lector.result; if (quitarFoto) quitarFoto.checked = false; pintar(); };
      lector.readAsDataURL(fi.files[0]);
    });
    // Vista compu / celular
    $$('[data-prev-ancho]').forEach(function (b) {
      b.addEventListener('click', function () {
        var marco = $('[data-prev-marco]'), w = b.getAttribute('data-prev-ancho');
        marco.style.maxWidth = w ? w + 'px' : '';
        marco.classList.toggle('es-celular', !!w);
        $$('[data-prev-ancho]').forEach(function (x) { x.classList.toggle('on', x === b); });
      });
    });
    pintar();
  }

  // ---------- Panel: marcar piezas para dar de baja / volver a la venta / eliminar ----------
  $$('[data-sel-piezas]').forEach(function (form) {
    var casillas = $$('.ad-pieza-sel', form), cuenta = $('[data-sel-cuenta]', form);
    var motivo = $('[data-sel-motivo]', form), otro = $('[data-sel-otro]', form);
    var pintar = function () {
      var marcadas = casillas.filter(function (c) { return c.checked; });
      $$('[data-sel-cuenta]', form).forEach(function (c) { c.textContent = marcadas.length; });
      var vf = $('[data-venta-fuera]', form);
      if (vf) {
        var libres = marcadas.filter(function (c) { return c.dataset.tipo === 'libre'; });
        var g = libres.reduce(function (t, c) { return t + (parseInt(c.dataset.peso, 10) || 0); }, 0);
        var est = Math.round(parseFloat(vf.dataset.precioKg || '0') * g / 1000);
        $$('[data-sel-cuenta]', vf).forEach(function (c) { c.textContent = libres.length; });
        var imp = $('[data-venta-importe]', vf);
        if (imp) imp.placeholder = est ? '$ ' + est.toLocaleString('es-AR') + ' a precio de lista' : '$';
      }
      $$('[data-sel-plural]', form).forEach(function (s) { s.hidden = marcadas.length === 1; });
      $$('[data-sel-btn]', form).forEach(function (b) {
        var tipos = b.getAttribute('data-sel-btn').split(' ');
        b.disabled = !marcadas.some(function (c) { return tipos.indexOf(c.dataset.tipo) !== -1; });
      });
      casillas.forEach(function (c) { c.closest('.ad-pieza').classList.toggle('sel', c.checked); });
    };
    casillas.forEach(function (c) { c.addEventListener('change', pintar); });
    $$('[data-sel-marcar]', form).forEach(function (b) {
      b.addEventListener('click', function () {
        var tipo = b.getAttribute('data-sel-marcar');
        casillas.forEach(function (c) { c.checked = !!tipo && c.dataset.tipo === tipo; });
        pintar();
      });
    });
    if (motivo && otro) motivo.addEventListener('change', function () { otro.hidden = motivo.value !== 'otro'; if (!otro.hidden) otro.focus(); });
    $$('[data-confirmar]', form).forEach(function (b) {
      b.addEventListener('click', function (e) { if (!window.confirm(b.getAttribute('data-confirmar'))) e.preventDefault(); });
    });
    var vender = $('button[value=vender]', form), canal = $('[data-venta-canal]', form);
    if (vender && canal) {
      vender.addEventListener('click', function (e) { if (!canal.value) { e.preventDefault(); canal.focus(); canal.classList.add('is-error'); } });
      canal.addEventListener('change', function () { canal.classList.remove('is-error'); });
      // Enter dentro del modal registra la venta (y no dispara otro botón de la barra)
      $$('input', canal.closest('[data-venta-fuera]')).forEach(function (i) {
        i.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); vender.click(); } });
      });
    }
    var baja = $('button[value=baja]', form);
    if (baja) baja.addEventListener('click', function (e) {
      if (!motivo.value || (motivo.value === 'otro' && !otro.value.trim())) { e.preventDefault(); motivo.focus(); motivo.classList.add('is-error'); }
    });
    pintar();
  });
})();
