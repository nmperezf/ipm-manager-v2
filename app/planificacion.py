"""Qué toca inspeccionar y cuándo.

Todo el módulo se apoya en una sola idea: **el calendario no se guarda, se
calcula**. Un contrato declara qué categorías cubre y desde qué mes ancla;
el catálogo declara qué cadencias existen dentro de cada categoría. Con
esas dos cosas se deriva qué rutina cae cada mes, para cualquier mes del
pasado o del futuro, sin filas de calendario que mantener sincronizadas.

Eso es lo que hace el sistema predictivo: se puede mostrar el año entero de
una instalación antes de que exista ninguna visita.
"""

from calendar import monthrange
from datetime import date

from app.models import (
    NIVEL_FRECUENCIA,
    OT_PENDIENTE,
    OT_PREVENTIVO,
    PERIODO_MESES,
    CampoFormulario,
    CategoriaEquipo,
    Cliente,
    Contrato,
    Instalacion,
    ItemVisita,
    OrdenTrabajo,
    ServicioContrato,
    TipoFormulario,
    Visita,
    db,
)

MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def frecuencias_de_categoria(categoria_id):
    """Qué cadencias define el catálogo para esa categoría.

    Sale de los campos, no de una tabla aparte: si un formulario de ECA
    tiene puntos semestrales, la categoría tiene rutina semestral. Así el
    calendario no puede desincronizarse del checklist.
    """
    filas = (
        db.session.query(CampoFormulario.frecuencia)
        .join(TipoFormulario, CampoFormulario.tipo_formulario_id == TipoFormulario.id)
        .filter(TipoFormulario.categoria_id == categoria_id)
        .filter(TipoFormulario.incluir_en_paquete.is_(True))
        .distinct()
        .all()
    )
    frecs = {f[0] for f in filas if f[0] in PERIODO_MESES}
    # Sin campos con frecuencia declarada, se cae a la del formulario.
    if not frecs:
        filas = (
            db.session.query(TipoFormulario.frecuencia)
            .filter(TipoFormulario.categoria_id == categoria_id)
            .distinct().all()
        )
        frecs = {f[0] for f in filas if f[0] in PERIODO_MESES}
    return sorted(frecs, key=lambda f: NIVEL_FRECUENCIA[f])


def rutina_del_mes(mes_ancla, anio, mes, frecuencias):
    """La rutina que cae ese mes, o None si no cae ninguna.

    Se elige la **más alta** cuyo período divida la distancia al ancla,
    porque las rutinas son acumulativas: si en septiembre cae la semestral,
    esa ya incluye todo lo mensual.
    """
    if not frecuencias:
        return None
    offset = (mes - mes_ancla) % 12
    elegida = None
    for frec in frecuencias:
        periodo = PERIODO_MESES[frec]
        cae = (offset == 0) if periodo == 12 else (offset % periodo == 0)
        if cae and (elegida is None or NIVEL_FRECUENCIA[frec] > NIVEL_FRECUENCIA[elegida]):
            elegida = frec
    return elegida


def calendario_anual(servicio, anio):
    """Los doce meses del año con la rutina que cae en cada uno."""
    frecuencias = frecuencias_de_categoria(servicio.categoria_id)
    return [
        (mes, rutina_del_mes(servicio.mes_ancla, anio, mes, frecuencias))
        for mes in range(1, 13)
    ]


def _ya_visitada(instalacion_id, categoria_id, anio, mes):
    """True si ya existe una visita de esa categoría en ese mes."""
    primero = date(anio, mes, 1)
    ultimo = date(anio, mes, monthrange(anio, mes)[1])
    return (
        db.session.query(ItemVisita.id)
        .join(Visita, ItemVisita.visita_id == Visita.id)
        .filter(
            Visita.instalacion_id == instalacion_id,
            ItemVisita.categoria_id == categoria_id,
            Visita.fecha >= primero,
            Visita.fecha <= ultimo,
        )
        .first()
        is not None
    )


def pendientes_del_mes(empresa_id, anio, mes):
    """Lo que el contrato manda hacer ese mes y todavía no se hizo.

    No hay tabla de solicitudes: se calcula contra los contratos vigentes.
    Sin filas intermedias no hay nada que pueda quedar viejo, y cambiar un
    contrato se refleja al instante.
    """
    servicios = (
        ServicioContrato.query
        .join(Contrato, ServicioContrato.contrato_id == Contrato.id)
        .join(Instalacion, Contrato.instalacion_id == Instalacion.id)
        .join(Cliente, Instalacion.cliente_id == Cliente.id)
        .filter(Cliente.empresa_id == empresa_id)
        .all()
    )

    referencia = date(anio, mes, min(15, monthrange(anio, mes)[1]))
    pendientes = []
    for servicio in servicios:
        contrato = servicio.contrato
        if not contrato.vigente_en(referencia):
            continue
        rutina = rutina_del_mes(
            servicio.mes_ancla, anio, mes, frecuencias_de_categoria(servicio.categoria_id)
        )
        if rutina is None:
            continue
        if _ya_visitada(contrato.instalacion_id, servicio.categoria_id, anio, mes):
            continue
        pendientes.append({
            "servicio": servicio,
            "contrato": contrato,
            "instalacion": contrato.instalacion,
            "categoria": servicio.categoria,
            "rutina": rutina,
        })

    pendientes.sort(key=lambda p: (p["instalacion"].cliente.nombre,
                                   p["instalacion"].nombre,
                                   p["categoria"].orden))
    return pendientes


def coordinar(servicio, fecha, rutina, tecnico, empresa_id):
    """Confirma una fecha y deja todo creado de una sola vez.

    Un click hace las tres cosas: la visita con su ítem en la rutina que
    corresponde, y la orden de trabajo numerada con la que el técnico va a
    llegar al checklist.
    """
    contrato = servicio.contrato
    visita = Visita(
        instalacion_id=contrato.instalacion_id,
        fecha=fecha,
        tecnico_id=tecnico.id if tecnico else None,
    )
    db.session.add(visita)
    db.session.flush()

    item = ItemVisita(visita_id=visita.id, categoria_id=servicio.categoria_id, rutina=rutina)
    db.session.add(item)

    orden = OrdenTrabajo(
        visita_id=visita.id,
        tipo=OT_PREVENTIVO,
        estado=OT_PENDIENTE,
        tecnico_id=tecnico.id if tecnico else None,
        fecha_apertura=date.today(),
        fecha_compromiso=fecha,
        descripcion=(
            f"{contrato.instalacion.cliente.nombre} · {contrato.instalacion.nombre} — "
            f"{servicio.categoria.nombre}, rutina {rutina}"
        ),
    )
    db.session.add(orden)
    db.session.flush()
    orden.asignar_numero(empresa_id)
    db.session.commit()
    return orden


def categorias_disponibles(empresa_id):
    return (
        CategoriaEquipo.query.filter_by(empresa_id=empresa_id)
        .order_by(CategoriaEquipo.orden).all()
    )
