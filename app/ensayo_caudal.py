"""Curva neta de la bomba: succión, corrección por afinidad, y comparación
directa contra los tres psi de placa (churn / 100 % / 150 %).

Nada de esto se guarda calculado — se recalcula siempre desde el dato
crudo (succión, descarga, RPM medido) más la placa vigente del equipo, así
que corregir la placa después de cargado el ensayo no deja un resultado
guardado desincronizado del que se vuelve a calcular.
"""

from app.models import CLASIF_CRITICA, PUNTO_100, PUNTO_150, PUNTO_CHURN, Observacion, db

_NOMBRE_PUNTO = {PUNTO_CHURN: "Churn", PUNTO_100: "100 % del caudal", PUNTO_150: "150 % del caudal"}

# Sin un número cerrado de norma para cuánto puede caer la velocidad del
# motor durante el ensayo, el criterio configurado es no perder más del
# 10 % de la nominal. Por debajo, el punto no cumple directamente: la
# corrección por afinidad ya no es confiable tan lejos de la velocidad
# de placa, sea cual sea la presión que haya dado.
PISO_RPM_PCT = 0.90

# Punto -> (atributo de placa, si el límite es un máximo o un mínimo).
# El 50 % no tiene entrada acá a propósito: ningún fabricante certifica
# un cuarto punto ahí, es solo referencia de forma de la curva.
_REFERENCIA_PUNTO = {
    PUNTO_CHURN: ("presion_maxima", "max"),
    PUNTO_100: ("presion_diseno", "min"),
    PUNTO_150: ("presion_sobrecarga", "min"),
}


def corregir(caudal, succion, descarga, rpm_medido, rpm_nominal):
    """(caudal, presión neta) corregidos a la velocidad de placa.

    Leyes de afinidad: el caudal escala lineal con la velocidad, la
    presión con el cuadrado. Sin RPM medido no hay con qué corregir, se
    devuelve el dato tal cual (factor 1).
    """
    neta = (descarga or 0) - (succion or 0)
    factor = (rpm_nominal / rpm_medido) if (rpm_medido and rpm_nominal) else 1
    return (caudal or 0) * factor, neta * factor * factor


def rpm_ok(rpm_medido, rpm_nominal):
    """False solo cuando hay dato y cae mas del 10% bajo la nominal."""
    if not rpm_medido or not rpm_nominal:
        return True
    return rpm_medido >= rpm_nominal * PISO_RPM_PCT


def referencia_de(equipo, etiqueta):
    """(valor_psi, 'max'|'min') según la placa, o None si no aplica —
    punto sin referencia (el 50 %) o dato de placa sin cargar."""
    conf = _REFERENCIA_PUNTO.get(etiqueta)
    if not conf:
        return None
    campo, tipo = conf
    valor = getattr(equipo, campo, None)
    if valor is None:
        return None
    return valor, tipo


def evaluar_punto(equipo, punto):
    """Evalúa un PuntoEnsayoCaudal contra la placa del equipo.

    None si todavía no hay succión y descarga cargadas (no se puede
    evaluar). Si el punto no tiene referencia de placa (el 50 %, o un
    extra), `ok`/`ok_presion` quedan en None: no es ni cumple ni no
    cumple, no se evalúa.
    """
    if punto.succion is None or punto.descarga is None:
        return None

    rpm_nominal = equipo.rpm_nominal
    q_corr, h_corr = corregir(punto.caudal, punto.succion, punto.descarga, punto.rpm, rpm_nominal)
    ok_rpm = rpm_ok(punto.rpm, rpm_nominal)

    ref = referencia_de(equipo, punto.etiqueta)
    if ref is None:
        return {
            "q_corregido": q_corr, "h_corregido": h_corr,
            "ok_rpm": ok_rpm, "referencia": None, "tipo_referencia": None,
            "ok_presion": None, "ok": None,
        }

    valor_ref, tipo_ref = ref
    ok_presion = h_corr <= valor_ref if tipo_ref == "max" else h_corr >= valor_ref
    return {
        "q_corregido": q_corr, "h_corregido": h_corr,
        "ok_rpm": ok_rpm, "referencia": valor_ref, "tipo_referencia": tipo_ref,
        "ok_presion": ok_presion, "ok": ok_presion and ok_rpm,
    }


def actualizar_observacion(punto, resultado, equipo, instalacion, item, usuario, aprueba_solo):
    """Abre, actualiza o cierra la deficiencia ligada a este punto.

    Solo los tres puntos con referencia de placa (churn/100/150) pueden
    generar una: el 50 % y los extras no tienen contra qué compararse
    (`resultado["ok"]` da None), así que nunca disparan nada. Un pump
    fuera de su curva certificada es un hallazgo de vida-seguridad, no
    un matiz — nace siempre como crítica, no queda a elección del
    técnico como en el resto del checklist.
    """
    existente = punto.observacion

    if resultado is None or resultado.get("ok") is not False:
        if existente:
            db.session.delete(existente)
        return False

    motivo = "no llegó a velocidad" if not resultado["ok_rpm"] else "fuera del rango de placa"
    nombre = _NOMBRE_PUNTO.get(punto.etiqueta, punto.etiqueta)
    referencia = (
        f", referencia {resultado['referencia']:.0f} psi" if resultado["referencia"] is not None else ""
    )
    descripcion = (
        f"Prueba anual de caudal — {nombre}: {motivo} "
        f"({resultado['h_corregido']:.1f} psi corregido{referencia})."
    )

    if existente:
        existente.descripcion = descripcion
        return False

    observacion = Observacion(
        instalacion_id=instalacion.id,
        equipo_id=equipo.id,
        punto_ensayo_id=punto.id,
        visita_id=item.visita_id,
        clasificacion=CLASIF_CRITICA,
        descripcion=descripcion,
        creado_por_id=usuario.id if usuario else None,
    )
    if aprueba_solo:
        observacion.aprobar(usuario, automatica=True)
    db.session.add(observacion)
    return True
