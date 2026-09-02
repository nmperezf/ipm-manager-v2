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

from app.models import (
    CAMPO_NUMERO,
    CampoFormulario,
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
