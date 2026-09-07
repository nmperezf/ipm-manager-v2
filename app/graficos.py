"""Evolución de los valores numéricos de un equipo.

Los gráficos se calculan acá y se dibujan como SVG inline en la plantilla:
sin librería de gráficos, sin CDN, sin JavaScript. Una sala de bombas no
tiene señal, y un gráfico que depende de bajar cientos de KB no se ve justo
cuando hace falta.

Lo que se grafica es la serie de un `CampoFormulario` numérico para un
`Equipo` concreto: la presión de descarga de *esa* bomba visita tras
visita. Es lo que deja ver una degradación lenta que ninguna inspección
suelta muestra.
"""

from app.ensayo_caudal import corregir
from app.models import (
    CAMPO_NUMERO,
    PUNTO_150,
    PUNTOS_FIJOS,
    CampoFormulario,
    EnsayoCaudal,
    Formulario,
    ItemVisita,
    Respuesta,
    TipoFormulario,
    Visita,
    db,
)

# Geometría del dibujo. El SVG se escala solo por viewBox.
ANCHO = 520
ALTO = 150
PAD_X = 34
PAD_Y = 16

# Geometría de la curva de caudal: más ancha, para que los cuatro puntos
# (churn/50/100/150) no queden apretados.
CURVA_ANCHO = 620
CURVA_ALTO = 300
CURVA_PAD_X = 56
CURVA_PAD_Y = 24

# Un color por año, del más viejo (gris azulado apagado) al más nuevo
# (azul de marca) — la línea más reciente es la que importa, así que es
# la más saturada. Si a una bomba se le viene cayendo la curva año a
# año, esto tiene que leerse de un vistazo.
_PALETA_ANIOS = ["#C7D2DC", "#9FB0C0", "#7CA8CB", "#5B94BF", "#3F86C2", "#2E6DA4"]


def campos_numericos(equipo):
    """Campos numéricos que aplican al tipo de este equipo."""
    if equipo is None or equipo.tipo_equipo_id is None:
        return []
    return (
        CampoFormulario.query
        .join(TipoFormulario, CampoFormulario.tipo_formulario_id == TipoFormulario.id)
        .filter(
            TipoFormulario.tipo_equipo_id == equipo.tipo_equipo_id,
            CampoFormulario.tipo == CAMPO_NUMERO,
        )
        .order_by(TipoFormulario.orden, CampoFormulario.orden)
        .all()
    )


def serie_numerica(equipo, campo):
    """Los valores de ese campo para ese equipo, del más viejo al más nuevo."""
    filas = (
        db.session.query(Visita.fecha, Respuesta.valor_numero, Respuesta.estado)
        .join(ItemVisita, ItemVisita.visita_id == Visita.id)
        .join(Formulario, Formulario.item_visita_id == ItemVisita.id)
        .join(Respuesta, Respuesta.formulario_id == Formulario.id)
        .filter(
            Formulario.equipo_id == equipo.id,
            Respuesta.campo_id == campo.id,
            Respuesta.valor_numero.isnot(None),
        )
        .order_by(Visita.fecha, Visita.id)
        .all()
    )
    return [
        {"fecha": fecha, "valor": valor, "estado": estado}
        for fecha, valor, estado in filas
    ]


def preparar_grafico(serie, campo):
    """Convierte la serie en coordenadas listas para el SVG.

    Devuelve None con menos de dos puntos: una línea de un solo valor no
    dice nada, y dibujarla igual sugiere una tendencia que no existe.
    """
    if len(serie) < 2:
        return None

    valores = [p["valor"] for p in serie]
    minimo, maximo = min(valores), max(valores)

    # El rango del eje incluye los umbrales del campo, si los tiene: sin
    # eso, un valor siempre conforme se dibuja igual que uno al borde.
    referencias = []
    if campo.minimo is not None:
        referencias.append(("mín.", campo.minimo))
        minimo = min(minimo, campo.minimo)
        maximo = max(maximo, campo.minimo)
    if campo.maximo is not None:
        referencias.append(("máx.", campo.maximo))
        minimo = min(minimo, campo.maximo)
        maximo = max(maximo, campo.maximo)

    # Aire arriba y abajo, y protección contra una serie plana.
    span = maximo - minimo
    if span == 0:
        span = abs(maximo) * 0.1 or 1.0
        minimo -= span / 2
        maximo += span / 2
    else:
        minimo -= span * 0.1
        maximo += span * 0.1
    span = maximo - minimo

    util_x = ANCHO - PAD_X * 2
    util_y = ALTO - PAD_Y * 2

    def x_de(i):
        return PAD_X + (i / (len(serie) - 1)) * util_x

    def y_de(valor):
        return PAD_Y + util_y - ((valor - minimo) / span) * util_y

    puntos = [
        {
            "x": round(x_de(i), 1),
            "y": round(y_de(p["valor"]), 1),
            "valor": p["valor"],
            "fecha": p["fecha"],
            "alerta": campo.fuera_de_rango(p["valor"]),
        }
        for i, p in enumerate(serie)
    ]

    return {
        "campo": campo,
        "puntos": puntos,
        "linea": " ".join(f"{p['x']},{p['y']}" for p in puntos),
        # Área bajo la curva: da volumen sin competir con la línea.
        "area": (
            f"{puntos[0]['x']},{ALTO - PAD_Y} "
            + " ".join(f"{p['x']},{p['y']}" for p in puntos)
            + f" {puntos[-1]['x']},{ALTO - PAD_Y}"
        ),
        "referencias": [
            {"etiqueta": etiqueta, "valor": valor, "y": round(y_de(valor), 1)}
            for etiqueta, valor in referencias
        ],
        "eje": {
            "min": round(minimo, 1), "max": round(maximo, 1),
            "y_min": ALTO - PAD_Y, "y_max": PAD_Y,
        },
        "ancho": ANCHO, "alto": ALTO, "pad_x": PAD_X,
        "primero": puntos[0],
        "ultimo": puntos[-1],
        "hay_alerta": any(p["alerta"] for p in puntos),
    }


def graficos_de_equipo(equipo):
    """Todos los gráficos que tengan sentido para ese equipo."""
    salida = []
    for campo in campos_numericos(equipo):
        grafico = preparar_grafico(serie_numerica(equipo, campo), campo)
        if grafico:
            salida.append(grafico)
    return salida


def _suavizar(puntos):
    """Catmull-Rom a Bézier cúbica: una curva suave que pasa por todos los
    puntos dados, en vez de segmentos rectos entre uno y el siguiente —
    la forma real de una curva de bomba no tiene quiebres duros."""
    if len(puntos) < 3:
        return "M " + " L ".join(f"{x},{y}" for x, y in puntos)
    trazo = [f"M {puntos[0][0]},{puntos[0][1]}"]
    for i in range(len(puntos) - 1):
        p0 = puntos[i - 1] if i > 0 else puntos[i]
        p1 = puntos[i]
        p2 = puntos[i + 1]
        p3 = puntos[i + 2] if i + 2 < len(puntos) else p2
        c1x, c1y = p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6
        c2x, c2y = p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6
        trazo.append(f"C {c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} {p2[0]},{p2[1]}")
    return " ".join(trazo)


def curva_caudal_equipo(equipo):
    """Curvas de caudal (churn/50/100/150, corregidas por RPM) de todos los
    ensayos anuales de este equipo, una superpuesta a la otra, contra la
    curva de diseño de placa.

    None si no hay ningún ensayo cargado o si la placa no tiene RPM
    nominal (sin eso no hay con qué corregir el caudal medido).
    """
    if not equipo.rpm_nominal:
        return None

    filas = (
        db.session.query(EnsayoCaudal, Visita.fecha)
        .join(ItemVisita, EnsayoCaudal.item_visita_id == ItemVisita.id)
        .join(Visita, ItemVisita.visita_id == Visita.id)
        .filter(EnsayoCaudal.equipo_id == equipo.id)
        .order_by(Visita.fecha)
        .all()
    )
    if not filas:
        return None

    curvas_crudas = []
    for ensayo, fecha in filas:
        puntos = [p for p in ensayo.puntos if p.etiqueta in PUNTOS_FIJOS]
        puntos.sort(key=lambda p: p.orden)
        serie = []
        punto_150 = None
        for p in puntos:
            if p.succion is None or p.descarga is None:
                continue
            q, h = corregir(p.caudal, p.succion, p.descarga, p.rpm, equipo.rpm_nominal)
            serie.append((q, h))
            if p.etiqueta == PUNTO_150:
                punto_150 = h
        if len(serie) >= 2:
            curvas_crudas.append({"anio": fecha.year, "serie": serie, "h_150": punto_150})

    if not curvas_crudas:
        return None

    # Curva de diseño: los tres puntos de placa que sirven de referencia.
    # Sin caudal nominal no hay eje X para ubicarla, se omite (sigue
    # habiendo curvas medidas para mostrar, solo que sin la de fábrica).
    diseno = None
    if equipo.caudal_nominal:
        crudos_diseno = []
        if equipo.presion_maxima is not None:
            crudos_diseno.append((0, equipo.presion_maxima))
        if equipo.presion_diseno is not None:
            crudos_diseno.append((equipo.caudal_nominal, equipo.presion_diseno))
        if equipo.presion_sobrecarga is not None:
            crudos_diseno.append((equipo.caudal_nominal * 1.5, equipo.presion_sobrecarga))
        if len(crudos_diseno) >= 2:
            diseno = crudos_diseno

    todos_los_puntos = [pt for c in curvas_crudas for pt in c["serie"]] + (diseno or [])
    qs = [q for q, _ in todos_los_puntos]
    hs = [h for _, h in todos_los_puntos]
    q_min, q_max = min(0, min(qs)), max(qs)
    h_min, h_max = min(hs), max(hs)
    span_h = (h_max - h_min) or (abs(h_max) * 0.1 or 1.0)
    h_min -= span_h * 0.1
    h_max += span_h * 0.1
    span_h = h_max - h_min
    span_q = q_max - q_min or 1.0

    util_x = CURVA_ANCHO - CURVA_PAD_X * 2
    util_y = CURVA_ALTO - CURVA_PAD_Y * 2

    def x_de(q):
        return round(CURVA_PAD_X + ((q - q_min) / span_q) * util_x, 1)

    def y_de(h):
        return round(CURVA_PAD_Y + util_y - ((h - h_min) / span_h) * util_y, 1)

    # Colores por año: los últimos N tonos de la paleta, del más apagado
    # al más saturado — la curva más reciente es la que más importa.
    n = len(curvas_crudas)
    colores = (_PALETA_ANIOS * n)[-n:] if n <= len(_PALETA_ANIOS) else _PALETA_ANIOS[-1:] * n

    # La curva más reciente se resalta en rojo si no llega a la placa a
    # 150 % — un pump fuera de su curva certificada no es un matiz (mismo
    # criterio que actualizar_observacion en ensayo_caudal.py).
    critica = False
    ultima = curvas_crudas[-1]
    if equipo.presion_sobrecarga is not None and ultima["h_150"] is not None:
        critica = ultima["h_150"] < equipo.presion_sobrecarga

    anios = []
    for i, c in enumerate(curvas_crudas):
        es_ultima = i == len(curvas_crudas) - 1
        anios.append({
            "anio": c["anio"],
            "color": "var(--ember)" if (es_ultima and critica) else colores[i],
            "linea": _suavizar([(x_de(q), y_de(h)) for q, h in c["serie"]]),
            "puntos": [{"x": x_de(q), "y": y_de(h)} for q, h in c["serie"]],
            "destacada": es_ultima,
        })

    aviso = None
    if len(curvas_crudas) >= 2:
        primera, ultima_h = curvas_crudas[0]["h_150"], ultima["h_150"]
        if primera and ultima_h and primera > 0:
            caida_pct = (primera - ultima_h) / primera * 100
            if caida_pct >= 15:
                aviso = (
                    f"Caída sostenida: a 150 % del caudal la presión bajó de {primera:.0f} "
                    f"a {ultima_h:.0f} psi entre {curvas_crudas[0]['anio']} y {ultima['anio']} "
                    f"(-{caida_pct:.0f} %)."
                )

    return {
        "anios": anios,
        "diseno": (_suavizar([(x_de(q), y_de(h)) for q, h in diseno]) if diseno else None),
        "ancho": CURVA_ANCHO, "alto": CURVA_ALTO,
        "eje_y": {"min": round(h_min), "max": round(h_max)},
        "primero": filas[0][1], "ultimo": filas[-1][1],
        "aviso": aviso, "critica": critica,
    }
