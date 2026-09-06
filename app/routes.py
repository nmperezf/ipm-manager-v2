"""Rutas de IPM Manager v2.

Estructura del CMMS: panorama, clientes e instalaciones, visitas con su
checklist, banco de deficiencias y catálogo de la empresa.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import (
    Blueprint, Response, abort, current_app, flash, jsonify, redirect,
    render_template, request, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from app.checklist import armar_bloques, guardar_checklist, nombre_campo
from app.ensayo_caudal import actualizar_observacion, evaluar_punto
from app.exportar import csv_response
from app.fotos import FotoInvalida, borrar_archivo, guardar_archivo, guardar_firma, ruta_relativa
from app.graficos import graficos_de_equipo
from app.informes import generar_informe_visita
from app.inventario import StockInsuficiente, registrar_consumo, reponer_stock, repuestos_criticos
from app.notificaciones import notificar_gestion, notificar_usuario
from app.presupuestos import crear_presupuesto
from app.planificacion import (
    MESES,
    calendario_anual,
    categorias_disponibles,
    coordinar_solicitud,
    generar_solicitudes_mes,
)
from app.models import (
    CAMPO_ESTADO,
    CAMPO_MULTI,
    CAMPO_NUMERO,
    CAMPO_SELECCION,
    CLASIF_CRITICA,
    CLASIF_NO_CRITICA,
    ESTADO_CONFORME,
    ESTADO_NA,
    ESTADO_NO_CONFORME,
    ATRIBUTOS_EQUIPO,
    ESTADOS_PRESUPUESTO,
    FRECUENCIA_MENSUAL,
    FRECUENCIAS,
    GRAVEDAD_CRITICA,
    GRAVEDAD_NO_CRITICA,
    PRESUP_APROBADO,
    PRESUP_CERRADO,
    REVISION_APROBADA,
    REVISION_PENDIENTE,
    VISIBILIDAD_INTERNA,
    ESTADOS_OT,
    OT_CERRADA,
    OT_EN_CURSO,
    OT_EN_REVISION,
    OT_PENDIENTE,
    OT_PREVENTIVO,
    PUNTOS_FIJOS,
    CampoFormulario,
    CategoriaEquipo,
    Cliente,
    ConsumoRepuesto,
    CoordinacionAudit,
    Contrato,
    EnsayoCaudal,
    Equipo,
    Foto,
    HabilitacionTecnico,
    Instalacion,
    Formulario,
    ItemVisita,
    Notificacion,
    Observacion,
    OrdenTrabajo,
    Presupuesto,
    PuntoEnsayoCaudal,
    Repuesto,
    Respuesta,
    ROLES,
    ServicioContrato,
    SolicitudCoordinacion,
    TipoEquipo,
    TipoFormulario,
    Usuario,
    Visita,
    db,
)

principal = Blueprint("principal", __name__)


# ---------------------------------------------------------------------------
# Contexto compartido
# ---------------------------------------------------------------------------


@principal.app_context_processor
def inyectar_contexto():
    """Las constantes de dominio viajan a las plantillas para que el HTML no
    tenga literales sueltos que se desincronicen del modelo. El contador de
    pendientes alimenta el badge del riel y de la campana."""
    contexto = {
        "CAMPO_NUMERO": CAMPO_NUMERO,
        "CAMPO_ESTADO": CAMPO_ESTADO,
        "CAMPO_SELECCION": CAMPO_SELECCION,
        "CAMPO_MULTI": CAMPO_MULTI,
        "ESTADO_CONFORME": ESTADO_CONFORME,
        "ESTADO_NO_CONFORME": ESTADO_NO_CONFORME,
        "ESTADO_NA": ESTADO_NA,
        "GRAVEDAD_CRITICA": GRAVEDAD_CRITICA,
        "GRAVEDAD_NO_CRITICA": GRAVEDAD_NO_CRITICA,
        "CLASIF_CRITICA": CLASIF_CRITICA,
        "CLASIF_NO_CRITICA": CLASIF_NO_CRITICA,
        "REVISION_APROBADA": REVISION_APROBADA,
        "FRECUENCIAS": FRECUENCIAS,
        "ATRIBUTOS_EQUIPO": ATRIBUTOS_EQUIPO,
        "ESTADOS_OT": ESTADOS_OT,
        "OT_PENDIENTE": OT_PENDIENTE,
        "OT_EN_CURSO": OT_EN_CURSO,
        "OT_EN_REVISION": OT_EN_REVISION,
        "OT_CERRADA": OT_CERRADA,
        "ESTADOS_PRESUPUESTO": ESTADOS_PRESUPUESTO,
        "PRESUP_CERRADO": PRESUP_CERRADO,
        "nombre_campo": nombre_campo,
        "pendientes_aprobacion": 0,
        "notificaciones_no_leidas": 0,
    }
    if current_user.is_authenticated:
        contexto["pendientes_aprobacion"] = _observaciones_empresa().filter(
            Observacion.estado_revision == REVISION_PENDIENTE
        ).count()
        contexto["notificaciones_no_leidas"] = Notificacion.query.filter_by(
            destinatario_id=current_user.id, leido=False
        ).count()
    return contexto


# Lo único que alcanza un usuario del lado del cliente. Es lista blanca a
# propósito: una ruta nueva queda bloqueada por omisión en vez de quedar
# expuesta por olvido. Antes de esto, un usuario con rol Cliente entraba a
# toda la operación interna, incluidos los datos de OTROS clientes.
RUTAS_CLIENTE = {
    "principal.login",
    "principal.logout",
    "principal.cuenta",
    "principal.portal",
    "principal.visita_informe",
}


@principal.before_request
def _cerrar_para_clientes():
    if current_user.is_authenticated and current_user.es_cliente:
        if request.endpoint not in RUTAS_CLIENTE:
            abort(403)
    return None


def _verificar_empresa(empresa_id):
    if empresa_id != current_user.empresa_id:
        abort(403)


def _deficiencias_visibles(cliente):
    """Lo que el cliente tiene derecho a ver.

    Es el criterio que el modelo ya definía en `visible_para_cliente` y que
    hasta ahora ninguna vista consultaba: solo lo aprobado por un jefe
    técnico, y nunca lo marcado como interno.
    """
    return (
        Observacion.query
        .join(Instalacion, Observacion.instalacion_id == Instalacion.id)
        .filter(
            Instalacion.cliente_id == cliente.id,
            Observacion.estado_revision == REVISION_APROBADA,
            Observacion.visibilidad != VISIBILIDAD_INTERNA,
        )
        .order_by(Observacion.resuelto, Observacion.fecha_carga.desc())
    )


def _instalaciones_empresa():
    return Instalacion.query.join(Cliente).filter(Cliente.empresa_id == current_user.empresa_id)


def _observaciones_empresa():
    return (
        Observacion.query.join(Instalacion, Observacion.instalacion_id == Instalacion.id)
        .join(Cliente, Instalacion.cliente_id == Cliente.id)
        .filter(Cliente.empresa_id == current_user.empresa_id)
    )


def _visitas_empresa():
    return (
        Visita.query.join(Instalacion, Visita.instalacion_id == Instalacion.id)
        .join(Cliente, Instalacion.cliente_id == Cliente.id)
        .filter(Cliente.empresa_id == current_user.empresa_id)
    )


def categorias_de(instalacion):
    """Categorías con al menos un equipo cargado en esa instalación. Es lo
    que decide qué se puede inspeccionar ahí."""
    ids = {
        e.tipo_equipo.categoria_id
        for e in instalacion.equipos
        if e.activo and e.tipo_equipo and e.tipo_equipo.categoria_id
    }
    if not ids:
        return []
    return (
        CategoriaEquipo.query.filter(CategoriaEquipo.id.in_(ids))
        .order_by(CategoriaEquipo.orden)
        .all()
    )


principal.add_app_template_global(categorias_de, "categorias_de")


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------


@principal.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(
            "principal.portal" if current_user.es_cliente else "principal.inicio"))
    if request.method == "POST":
        usuario = Usuario.query.filter_by(
            username=request.form.get("username", "").strip()
        ).first()
        if usuario and usuario.activo and usuario.check_password(request.form.get("password", "")):
            login_user(usuario)
            # El cliente no tiene panorama interno: va directo a lo suyo.
            destino = "principal.portal" if usuario.es_cliente else "principal.inicio"
            return redirect(url_for(destino))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@principal.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("principal.login"))


# ---------------------------------------------------------------------------
# Panorama
# ---------------------------------------------------------------------------


@principal.route("/")
@login_required
def inicio():
    obs = _observaciones_empresa()
    criticas = (
        obs.filter(Observacion.clasificacion == CLASIF_CRITICA, Observacion.resuelto.is_(False))
        .order_by(Observacion.fecha_carga.desc()).all()
    )
    pendientes = obs.filter(Observacion.estado_revision == REVISION_PENDIENTE).count()
    abiertas = obs.filter(Observacion.resuelto.is_(False)).count()

    desde = date.today() - timedelta(days=30)
    visitas_recientes = (
        _visitas_empresa().filter(Visita.fecha >= desde)
        .order_by(Visita.fecha.desc(), Visita.id.desc()).limit(8).all()
    )
    total_visitas = _visitas_empresa().filter(Visita.fecha >= desde).count()
    instalaciones = _instalaciones_empresa().count()

    # Lo primero que necesita ver un técnico es qué tiene pendiente.
    q_ot = _ordenes_empresa().filter(OrdenTrabajo.estado != OT_CERRADA)
    if current_user.rol == "Técnico":
        q_ot = q_ot.filter(OrdenTrabajo.tecnico_id == current_user.id)
    mis_ordenes = q_ot.order_by(
        OrdenTrabajo.fecha_compromiso.asc().nullslast(), OrdenTrabajo.id.desc()
    ).limit(6).all()
    total_ordenes = q_ot.count()

    repuestos_criticos_lista = repuestos_criticos(current_user.empresa_id, limite=6)
    total_repuestos_criticos = len(repuestos_criticos(current_user.empresa_id))

    tecnicos_empresa = Usuario.query.filter_by(empresa_id=current_user.empresa_id, activo=True).all()
    habilitaciones_alerta = []
    for u in tecnicos_empresa:
        for h in u.habilitaciones_vencidas:
            habilitaciones_alerta.append((u, h, True))
        for h in u.habilitaciones_por_vencer:
            habilitaciones_alerta.append((u, h, False))

    # Si las tres secciones de "nada que atender" están vacías a la vez,
    # se funden en una sola franja liviana en vez de repetir tres tarjetas
    # casi idénticas (revisión UX sept. 2026).
    todo_al_dia = not mis_ordenes and not criticas and not visitas_recientes

    return render_template(
        "inicio.html",
        criticas=criticas[:6],
        total_criticas=len(criticas),
        pendientes=pendientes,
        abiertas=abiertas,
        visitas=visitas_recientes,
        total_visitas=total_visitas,
        instalaciones=instalaciones,
        mis_ordenes=mis_ordenes,
        total_ordenes=total_ordenes,
        repuestos_criticos=repuestos_criticos_lista,
        total_repuestos_criticos=total_repuestos_criticos,
        habilitaciones_alerta=habilitaciones_alerta,
        todo_al_dia=todo_al_dia,
    )


# ---------------------------------------------------------------------------
# Clientes e instalaciones
# ---------------------------------------------------------------------------


@principal.route("/clientes")
@login_required
def clientes():
    lista = (
        Cliente.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(Cliente.nombre).all()
    )
    return render_template("clientes.html", clientes=lista)


@principal.route("/cliente/<int:cliente_id>")
@login_required
def cliente(cliente_id):
    obj = db.session.get(Cliente, cliente_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)

    abiertas = (
        _observaciones_empresa()
        .filter(Cliente.id == obj.id, Observacion.resuelto.is_(False))
        .order_by(Observacion.fecha_carga.desc()).all()
    )
    return render_template("cliente.html", cliente=obj, abiertas=abiertas)


@principal.route("/instalacion/<int:instalacion_id>")
@login_required
def instalacion(instalacion_id):
    obj = db.session.get(Instalacion, instalacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.cliente.empresa_id)

    # Los equipos se listan como el técnico los recorre: los que encabezan
    # conjunto arrastran a sus hijos.
    activos = [e for e in obj.equipos if e.activo]
    raices = [e for e in activos if e.padre_id is None]
    raices.sort(key=lambda e: (e.tipo_equipo.orden, e.codigo or "", e.nombre))

    tecnicos = (
        Usuario.query.filter_by(empresa_id=current_user.empresa_id, activo=True)
        .filter(Usuario.rol.in_(("Técnico", "Jefe técnico")))
        .order_by(Usuario.nombre_completo).all()
    )
    visitas_inst = (
        Visita.query.filter_by(instalacion_id=obj.id)
        .order_by(Visita.fecha.desc(), Visita.id.desc()).limit(10).all()
    )
    abiertas = (
        Observacion.query.filter_by(instalacion_id=obj.id, resuelto=False)
        .order_by(Observacion.fecha_carga.desc()).all()
    )
    return render_template(
        "instalacion.html", instalacion=obj, raices=raices,
        visitas=visitas_inst, abiertas=abiertas, tecnicos=tecnicos,
    )


@principal.route("/instalacion/<int:instalacion_id>/inspeccionar/<int:categoria_id>", methods=["POST"])
@login_required
def abrir_inspeccion(instalacion_id, categoria_id):
    """Crea la visita y su ítem para arrancar a cargar. En el sistema
    terminado esto lo genera la coordinación mensual desde el contrato."""
    obj = db.session.get(Instalacion, instalacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.cliente.empresa_id)

    rutina = request.form.get("rutina", FRECUENCIA_MENSUAL)
    if rutina not in FRECUENCIAS:
        rutina = FRECUENCIA_MENSUAL

    tecnico_id = request.form.get("tecnico_id", type=int) or current_user.id

    nueva = Visita(instalacion_id=obj.id, fecha=date.today(), tecnico_id=tecnico_id)
    db.session.add(nueva)
    db.session.flush()
    item = ItemVisita(visita_id=nueva.id, categoria_id=categoria_id, rutina=rutina)
    db.session.add(item)

    # La OT es el compromiso que envuelve a la visita: es lo que se asigna,
    # se numera y el técnico ve en su lista.
    orden = OrdenTrabajo(
        visita_id=nueva.id,
        tipo=OT_PREVENTIVO,
        estado=OT_PENDIENTE,
        tecnico_id=tecnico_id,
        fecha_apertura=date.today(),
        fecha_compromiso=date.today(),
        descripcion=f"{obj.cliente.nombre} · {obj.nombre} — rutina {rutina}",
    )
    db.session.add(orden)
    db.session.flush()
    orden.asignar_numero(current_user.empresa_id)
    db.session.commit()

    flash(f"Orden {orden.numero} creada.", "ok")
    return redirect(url_for("principal.orden", orden_id=orden.id))


# ---------------------------------------------------------------------------
# Contratos
# ---------------------------------------------------------------------------


@principal.route("/instalacion/<int:instalacion_id>/contrato", methods=["GET", "POST"])
@login_required
def contrato_nuevo(instalacion_id):
    _solo_gestion()
    inst = db.session.get(Instalacion, instalacion_id)
    if inst is None:
        abort(404)
    _verificar_empresa(inst.cliente.empresa_id)
    return _guardar_contrato(inst, None)


@principal.route("/contrato/<int:contrato_id>/editar", methods=["GET", "POST"])
@login_required
def contrato_editar(contrato_id):
    _solo_gestion()
    obj = db.session.get(Contrato, contrato_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)
    return _guardar_contrato(obj.instalacion, obj)


def _guardar_contrato(inst, contrato):
    categorias = categorias_disponibles(current_user.empresa_id)

    if request.method == "POST":
        if contrato is None:
            contrato = Contrato(instalacion_id=inst.id)
            db.session.add(contrato)

        contrato.desde = _fecha(request.form.get("desde")) or date.today()
        contrato.hasta = _fecha(request.form.get("hasta"))
        contrato.activo = bool(request.form.get("activo"))
        contrato.notas = (request.form.get("notas") or "").strip() or None
        db.session.flush()

        # Las categorías cubiertas se reescriben enteras: es más simple que
        # diferenciar altas y bajas, y el servicio no guarda historia propia.
        existentes = {s.categoria_id: s for s in contrato.servicios}
        marcadas = set(request.form.getlist("categoria", type=int))
        for cat_id in marcadas:
            ancla = request.form.get(f"ancla_{cat_id}", type=int) or 1
            ancla = min(12, max(1, ancla))
            if cat_id in existentes:
                existentes[cat_id].mes_ancla = ancla
            else:
                db.session.add(ServicioContrato(
                    contrato_id=contrato.id, categoria_id=cat_id, mes_ancla=ancla))
        for cat_id, servicio in existentes.items():
            if cat_id not in marcadas:
                db.session.delete(servicio)

        db.session.commit()
        flash("Contrato guardado. El calendario se recalcula solo.", "ok")
        return redirect(url_for("principal.instalacion", instalacion_id=inst.id))

    anio = date.today().year
    calendarios = {
        s.categoria_id: calendario_anual(s, anio) for s in (contrato.servicios if contrato else [])
    }
    return render_template(
        "contrato_form.html", instalacion=inst, contrato=contrato,
        categorias=categorias, calendarios=calendarios, anio=anio, MESES=MESES,
    )


@principal.route("/contrato/<int:contrato_id>/baja", methods=["POST"])
@login_required
def contrato_baja(contrato_id):
    _solo_gestion()
    obj = db.session.get(Contrato, contrato_id)
    if obj is None:
        abort(404)
    inst = obj.instalacion
    _verificar_empresa(inst.cliente.empresa_id)
    obj.activo = False
    db.session.commit()
    flash("Contrato dado de baja. Deja de generar pendientes.", "ok")
    return redirect(url_for("principal.instalacion", instalacion_id=inst.id))


# ---------------------------------------------------------------------------
# Coordinación
# ---------------------------------------------------------------------------


def _solicitudes_empresa(anio, mes):
    return (
        SolicitudCoordinacion.query
        .join(ServicioContrato, SolicitudCoordinacion.servicio_id == ServicioContrato.id)
        .join(Contrato, ServicioContrato.contrato_id == Contrato.id)
        .join(Instalacion, Contrato.instalacion_id == Instalacion.id)
        .join(Cliente, Instalacion.cliente_id == Cliente.id)
        .filter(Cliente.empresa_id == current_user.empresa_id)
        .filter(SolicitudCoordinacion.anio == anio, SolicitudCoordinacion.mes == mes)
    )


@principal.route("/coordinacion")
@login_required
def coordinacion():
    """Lo que los contratos mandan hacer este mes.

    A diferencia de `pendientes_del_mes` (que se recalcula siempre y
    desaparece en cuanto se coordina), acá se persiste una
    SolicitudCoordinacion por servicio/mes — es lo que permite recoordinar
    una fecha ya confirmada sin perder el historial (ver CoordinacionAudit).
    """
    _solo_gestion()
    hoy = date.today()
    anio = request.args.get("anio", type=int) or hoy.year
    mes = request.args.get("mes", type=int) or hoy.month
    mes = min(12, max(1, mes))
    primero = date(anio, mes, 1)
    ultimo = date(anio, mes, monthrange(anio, mes)[1])

    solicitudes = _solicitudes_empresa(anio, mes).all()
    solicitudes.sort(key=lambda s: (
        s.estado_derivado != "sin_coordinar",
        s.servicio.contrato.instalacion.cliente.nombre,
    ))
    tecnicos = (
        Usuario.query.filter_by(empresa_id=current_user.empresa_id, activo=True)
        .filter(Usuario.rol.in_(("Técnico", "Jefe técnico")))
        .order_by(Usuario.nombre_completo).all()
    )

    return render_template(
        "coordinacion.html", solicitudes=solicitudes,
        tecnicos=tecnicos, anio=anio, mes=mes, MESES=MESES,
        hoy=hoy, sugerida=max(hoy, primero) if hoy <= ultimo else primero,
    )


@principal.route("/coordinacion/generar", methods=["POST"])
@login_required
def coordinacion_generar():
    _solo_gestion()
    anio = request.form.get("anio", type=int) or date.today().year
    mes = request.form.get("mes", type=int) or date.today().month
    creadas = generar_solicitudes_mes(current_user.empresa_id, anio, mes)
    flash(f"{creadas} solicitud(es) nueva(s) generada(s)." if creadas else
          "No hay solicitudes nuevas para generar.", "ok")
    return redirect(url_for("principal.coordinacion", anio=anio, mes=mes))


@principal.route("/solicitud/<int:solicitud_id>/coordinar", methods=["POST"])
@login_required
def solicitud_coordinar(solicitud_id):
    _solo_gestion()
    solicitud = db.session.get(SolicitudCoordinacion, solicitud_id)
    if solicitud is None:
        abort(404)
    _verificar_empresa(solicitud.servicio.contrato.instalacion.cliente.empresa_id)

    fecha = _fecha(request.form.get("fecha"))
    if fecha is None:
        flash("Hace falta una fecha para coordinar.", "error")
        return redirect(url_for("principal.coordinacion", anio=solicitud.anio, mes=solicitud.mes))

    notas = (request.form.get("notas") or "").strip() or None
    tecnico = db.session.get(Usuario, request.form.get("tecnico_id", type=int) or 0)
    if tecnico and tecnico.empresa_id != current_user.empresa_id:
        tecnico = None

    era_recoordinacion = bool(solicitud.orden_id)
    orden = coordinar_solicitud(solicitud, fecha, notas, current_user, tecnico=tecnico)
    if tecnico:
        notificar_usuario(
            tecnico, "ot_asignada", f"{orden.numero} asignada para el {fecha.strftime('%d/%m/%Y')}.",
            current_user.empresa_id, enlace=url_for("principal.orden", orden_id=orden.id),
            remitente=current_user,
        )
    flash(
        f"{orden.numero} recoordinada para el {fecha.strftime('%d/%m/%Y')}."
        if era_recoordinacion else
        f"{orden.numero} creada para el {fecha.strftime('%d/%m/%Y')}.",
        "ok",
    )
    return redirect(url_for("principal.coordinacion", anio=solicitud.anio, mes=solicitud.mes))


# ---------------------------------------------------------------------------
# Órdenes de trabajo
# ---------------------------------------------------------------------------


def _ordenes_empresa():
    return (
        OrdenTrabajo.query.join(Visita, OrdenTrabajo.visita_id == Visita.id)
        .join(Instalacion, Visita.instalacion_id == Instalacion.id)
        .join(Cliente, Instalacion.cliente_id == Cliente.id)
        .filter(Cliente.empresa_id == current_user.empresa_id)
    )


@principal.route("/ordenes")
@login_required
def ordenes():
    query = _ordenes_empresa()
    # Un técnico ve las suyas; gestión ve todas.
    if current_user.rol == "Técnico":
        query = query.filter(OrdenTrabajo.tecnico_id == current_user.id)

    filtro = request.args.get("estado")
    if filtro == "abiertas":
        query = query.filter(OrdenTrabajo.estado != OT_CERRADA)
    elif filtro in ESTADOS_OT:
        query = query.filter(OrdenTrabajo.estado == filtro)

    lista = query.order_by(
        OrdenTrabajo.fecha_compromiso.asc().nullslast(), OrdenTrabajo.id.desc()
    ).limit(80).all()
    return render_template("ordenes.html", ordenes=lista, filtro=filtro)


@principal.route("/ordenes/exportar")
@login_required
def ordenes_exportar():
    query = _ordenes_empresa()
    if current_user.rol == "Técnico":
        query = query.filter(OrdenTrabajo.tecnico_id == current_user.id)
    filtro = request.args.get("estado")
    if filtro == "abiertas":
        query = query.filter(OrdenTrabajo.estado != OT_CERRADA)
    elif filtro in ESTADOS_OT:
        query = query.filter(OrdenTrabajo.estado == filtro)

    ordenes_lista = query.order_by(OrdenTrabajo.fecha_apertura.desc()).all()
    filas = [
        [
            o.numero, o.tipo, o.prioridad, o.estado,
            o.visita.instalacion.cliente.nombre, o.visita.instalacion.nombre,
            o.tecnico.nombre_completo if o.tecnico else "",
            o.fecha_apertura.strftime("%d/%m/%Y"),
            o.fecha_compromiso.strftime("%d/%m/%Y") if o.fecha_compromiso else "",
            o.fecha_cierre.strftime("%d/%m/%Y") if o.fecha_cierre else "",
        ]
        for o in ordenes_lista
    ]
    return csv_response(
        "ordenes.csv",
        ["Número", "Tipo", "Prioridad", "Estado", "Cliente", "Instalación", "Técnico",
         "Apertura", "Compromiso", "Cierre"],
        filas,
    )


@principal.route("/orden/<int:orden_id>")
@login_required
def orden(orden_id):
    obj = db.session.get(OrdenTrabajo, orden_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.visita.instalacion.cliente.empresa_id)

    # El primer ítem sin cargar es a donde el técnico tiene que ir.
    siguiente = next((i for i in obj.visita.items if not i.formularios), None)
    repuestos_disponibles = _repuestos_empresa().order_by(Repuesto.nombre).all()
    return render_template(
        "orden.html", orden=obj, siguiente=siguiente, repuestos_disponibles=repuestos_disponibles,
    )


@principal.route("/orden/<int:orden_id>/estado", methods=["POST"])
@login_required
def orden_estado(orden_id):
    obj = db.session.get(OrdenTrabajo, orden_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.visita.instalacion.cliente.empresa_id)

    nuevo = request.form.get("estado", "")
    if not obj.puede_pasar_a(nuevo, current_user):
        flash(f"No se puede pasar de «{obj.estado}» a «{nuevo}».", "error")
        return redirect(url_for("principal.orden", orden_id=obj.id))

    obj.estado = nuevo
    obj.fecha_cierre = date.today() if nuevo == OT_CERRADA else None
    if nuevo == OT_CERRADA and obj.presupuesto_origen and obj.presupuesto_origen.estado != PRESUP_CERRADO:
        obj.presupuesto_origen.cambiar_estado(
            PRESUP_CERRADO, current_user, "Cerrado automáticamente al finalizar la OT."
        )
    db.session.commit()
    flash(f"{obj.numero} → {nuevo}.", "ok")
    return redirect(url_for("principal.orden", orden_id=obj.id))


# ---------------------------------------------------------------------------
# Altas y ediciones
#
# Ninguna devuelve a la lista: todas dejan al usuario donde sigue el
# trabajo. Volver al listado obliga a buscar a mano lo que se acaba de
# crear, y es la fricción que más se nota al cargar un cliente nuevo.
# ---------------------------------------------------------------------------


def _solo_gestion():
    if not current_user.puede_aprobar:
        abort(403)


def _fecha(valor):
    """Los <input type=date> llegan como aaaa-mm-dd, o vacíos."""
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        return None


def _numero(valor):
    """Los campos de placa son opcionales y vienen como texto del form."""
    valor = (valor or "").strip().replace(",", ".")
    if not valor:
        return None
    try:
        return float(valor)
    except ValueError:
        return None


@principal.route("/cliente/nuevo", methods=["GET", "POST"])
@login_required
def cliente_nuevo():
    """Alta unificada: cliente, su primera instalación y los equipos de esa
    instalación, todo en un solo guardado.

    Los equipos que se cargan acá son el alta rápida (tipo, código, nombre):
    los datos de placa y los conjuntos (padre_id) se completan después
    desde equipo_editar, igual que en el alta de equipo individual.
    """
    _solo_gestion()
    tipos_equipo = (
        TipoEquipo.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(TipoEquipo.orden, TipoEquipo.nombre).all()
    )

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre del cliente es obligatorio.", "error")
            return render_template("cliente_form.html", cliente=None, datos=request.form,
                                   tipos_equipo=tipos_equipo)

        nuevo = Cliente(
            empresa_id=current_user.empresa_id,
            nombre=nombre,
            rut=(request.form.get("rut") or "").strip() or None,
            contacto=(request.form.get("contacto") or "").strip() or None,
            telefono=(request.form.get("telefono") or "").strip() or None,
            email=(request.form.get("email") or "").strip() or None,
            direccion=(request.form.get("direccion") or "").strip() or None,
        )
        db.session.add(nuevo)
        db.session.flush()

        inst_nombre = (request.form.get("instalacion_nombre") or "").strip()
        creada = None
        if inst_nombre:
            creada = Instalacion(
                cliente_id=nuevo.id,
                nombre=inst_nombre,
                direccion=(request.form.get("instalacion_direccion") or "").strip()
                or nuevo.direccion,
            )
            db.session.add(creada)
            db.session.flush()

            tipos_ids = request.form.getlist("equipo_tipo_id")
            codigos = request.form.getlist("equipo_codigo")
            nombres = request.form.getlist("equipo_nombre")
            tipos_validos = {t.id for t in tipos_equipo}
            cuantos = 0
            for tipo_id_raw, codigo, nombre_equipo in zip(tipos_ids, codigos, nombres):
                nombre_equipo = (nombre_equipo or "").strip()
                tipo_id = int(tipo_id_raw) if tipo_id_raw and tipo_id_raw.isdigit() else None
                if not nombre_equipo or tipo_id not in tipos_validos:
                    continue
                db.session.add(Equipo(
                    instalacion_id=creada.id, tipo_equipo_id=tipo_id,
                    codigo=(codigo or "").strip() or None, nombre=nombre_equipo,
                ))
                cuantos += 1

        db.session.commit()

        if creada:
            if cuantos:
                flash(
                    f"Cliente, «{creada.nombre}» y {cuantos} equipo(s) creados. "
                    "Completá los datos de placa de cada uno cuando puedas.", "ok",
                )
            else:
                flash(f"Cliente e instalación creados. Cargá los equipos de «{creada.nombre}».", "ok")
            return redirect(url_for("principal.instalacion", instalacion_id=creada.id))
        flash("Cliente creado.", "ok")
        return redirect(url_for("principal.cliente", cliente_id=nuevo.id))

    return render_template("cliente_form.html", cliente=None, datos={}, tipos_equipo=tipos_equipo)


@principal.route("/cliente/<int:cliente_id>/editar", methods=["GET", "POST"])
@login_required
def cliente_editar(cliente_id):
    _solo_gestion()
    obj = db.session.get(Cliente, cliente_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre del cliente es obligatorio.", "error")
            return render_template("cliente_form.html", cliente=obj, datos=request.form)

        obj.nombre = nombre
        obj.rut = (request.form.get("rut") or "").strip() or None
        obj.contacto = (request.form.get("contacto") or "").strip() or None
        obj.telefono = (request.form.get("telefono") or "").strip() or None
        obj.email = (request.form.get("email") or "").strip() or None
        obj.direccion = (request.form.get("direccion") or "").strip() or None
        db.session.commit()
        flash("Cliente actualizado.", "ok")
        return redirect(url_for("principal.cliente", cliente_id=obj.id))

    return render_template("cliente_form.html", cliente=obj, datos={})


@principal.route("/cliente/<int:cliente_id>/instalacion/nueva", methods=["GET", "POST"])
@login_required
def instalacion_nueva(cliente_id):
    _solo_gestion()
    padre = db.session.get(Cliente, cliente_id)
    if padre is None:
        abort(404)
    _verificar_empresa(padre.empresa_id)

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre de la instalación es obligatorio.", "error")
            return render_template("instalacion_form.html", cliente=padre,
                                   instalacion=None, datos=request.form)

        creada = Instalacion(
            cliente_id=padre.id,
            nombre=nombre,
            direccion=(request.form.get("direccion") or "").strip() or padre.direccion,
        )
        db.session.add(creada)
        db.session.commit()
        flash("Instalación creada. Cargá sus equipos para poder inspeccionarla.", "ok")
        return redirect(url_for("principal.instalacion", instalacion_id=creada.id))

    return render_template("instalacion_form.html", cliente=padre, instalacion=None, datos={})


@principal.route("/instalacion/<int:instalacion_id>/editar", methods=["GET", "POST"])
@login_required
def instalacion_editar(instalacion_id):
    _solo_gestion()
    obj = db.session.get(Instalacion, instalacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.cliente.empresa_id)

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre de la instalación es obligatorio.", "error")
            return render_template("instalacion_form.html", cliente=obj.cliente,
                                   instalacion=obj, datos=request.form)
        obj.nombre = nombre
        obj.direccion = (request.form.get("direccion") or "").strip() or None
        db.session.commit()
        flash("Instalación actualizada.", "ok")
        return redirect(url_for("principal.instalacion", instalacion_id=obj.id))

    return render_template("instalacion_form.html", cliente=obj.cliente,
                           instalacion=obj, datos={})


def _opciones_equipo(inst, excluir_id=None):
    """Tipos del catálogo y posibles padres. Solo se ofrece como padre un
    equipo que encabece conjunto: es lo que hace que el controlador se
    recorra junto a su bomba y no suelto."""
    tipos = (
        TipoEquipo.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(TipoEquipo.categoria_id, TipoEquipo.orden).all()
    )
    padres = [
        e for e in inst.equipos
        if e.activo and e.tipo_equipo and e.tipo_equipo.encabeza_conjunto and e.id != excluir_id
    ]
    padres.sort(key=lambda e: (e.codigo or "", e.nombre))
    return tipos, padres


@principal.route("/instalacion/<int:instalacion_id>/equipo/nuevo", methods=["GET", "POST"])
@login_required
def equipo_nuevo(instalacion_id):
    _solo_gestion()
    inst = db.session.get(Instalacion, instalacion_id)
    if inst is None:
        abort(404)
    _verificar_empresa(inst.cliente.empresa_id)

    tipos, padres = _opciones_equipo(inst)

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        tipo_id = request.form.get("tipo_equipo_id", type=int)
        tipo = db.session.get(TipoEquipo, tipo_id) if tipo_id else None

        if not nombre or tipo is None or tipo.empresa_id != current_user.empresa_id:
            flash("Hacen falta un nombre y un tipo de equipo del catálogo.", "error")
            return render_template("equipo_form.html", instalacion=inst, equipo=None,
                                   tipos=tipos, padres=padres, datos=request.form)

        padre_id = request.form.get("padre_id", type=int) or None
        if padre_id and padre_id not in [p.id for p in padres]:
            padre_id = None

        creado = Equipo(
            instalacion_id=inst.id,
            tipo_equipo_id=tipo.id,
            padre_id=padre_id,
            codigo=(request.form.get("codigo") or "").strip() or None,
            nombre=nombre,
            ubicacion=(request.form.get("ubicacion") or "").strip() or None,
            marca=(request.form.get("marca") or "").strip() or None,
            modelo=(request.form.get("modelo") or "").strip() or None,
            serie=(request.form.get("serie") or "").strip() or None,
            caudal_nominal=_numero(request.form.get("caudal_nominal")),
            presion_diseno=_numero(request.form.get("presion_diseno")),
            presion_maxima=_numero(request.form.get("presion_maxima")),
            presion_sobrecarga=_numero(request.form.get("presion_sobrecarga")),
            rpm_nominal=int(_numero(request.form.get("rpm_nominal")) or 0) or None,
        )
        db.session.add(creado)
        db.session.commit()
        flash(f"Equipo «{creado.etiqueta}» agregado.", "ok")
        # Cargar equipos es repetitivo: se puede volver al mismo formulario.
        if request.form.get("otro"):
            return redirect(url_for("principal.equipo_nuevo", instalacion_id=inst.id))
        return redirect(url_for("principal.instalacion", instalacion_id=inst.id))

    return render_template("equipo_form.html", instalacion=inst, equipo=None,
                           tipos=tipos, padres=padres, datos={})


@principal.route("/equipo/<int:equipo_id>/editar", methods=["GET", "POST"])
@login_required
def equipo_editar(equipo_id):
    _solo_gestion()
    obj = db.session.get(Equipo, equipo_id)
    if obj is None:
        abort(404)
    inst = obj.instalacion
    _verificar_empresa(inst.cliente.empresa_id)

    tipos, padres = _opciones_equipo(inst, excluir_id=obj.id)

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        tipo_id = request.form.get("tipo_equipo_id", type=int)
        tipo = db.session.get(TipoEquipo, tipo_id) if tipo_id else None

        if not nombre or tipo is None or tipo.empresa_id != current_user.empresa_id:
            flash("Hacen falta un nombre y un tipo de equipo del catálogo.", "error")
            return render_template("equipo_form.html", instalacion=inst, equipo=obj,
                                   tipos=tipos, padres=padres, datos=request.form)

        padre_id = request.form.get("padre_id", type=int) or None
        if padre_id and padre_id not in [p.id for p in padres]:
            padre_id = None

        obj.tipo_equipo_id = tipo.id
        obj.padre_id = padre_id
        obj.codigo = (request.form.get("codigo") or "").strip() or None
        obj.nombre = nombre
        obj.ubicacion = (request.form.get("ubicacion") or "").strip() or None
        obj.marca = (request.form.get("marca") or "").strip() or None
        obj.modelo = (request.form.get("modelo") or "").strip() or None
        obj.serie = (request.form.get("serie") or "").strip() or None
        obj.caudal_nominal = _numero(request.form.get("caudal_nominal"))
        obj.presion_diseno = _numero(request.form.get("presion_diseno"))
        obj.presion_maxima = _numero(request.form.get("presion_maxima"))
        obj.presion_sobrecarga = _numero(request.form.get("presion_sobrecarga"))
        obj.rpm_nominal = int(_numero(request.form.get("rpm_nominal")) or 0) or None
        db.session.commit()
        flash("Equipo actualizado.", "ok")
        return redirect(url_for("principal.instalacion", instalacion_id=inst.id))

    return render_template("equipo_form.html", instalacion=inst, equipo=obj,
                           tipos=tipos, padres=padres, datos={})


@principal.route("/equipo/<int:equipo_id>")
@login_required
def equipo_detalle(equipo_id):
    """Ficha del equipo: su placa, la evolución de sus valores numéricos,
    sus fotos y sus deficiencias abiertas."""
    obj = db.session.get(Equipo, equipo_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)

    fotos = (
        Foto.query.filter_by(equipo_id=obj.id)
        .order_by(Foto.fecha.desc()).limit(12).all()
    )
    abiertas = (
        Observacion.query.filter_by(equipo_id=obj.id, resuelto=False)
        .order_by(Observacion.fecha_carga.desc()).all()
    )
    return render_template(
        "equipo.html", equipo=obj, graficos=graficos_de_equipo(obj),
        fotos=fotos, abiertas=abiertas,
    )


@principal.route("/equipo/<int:equipo_id>/baja", methods=["POST"])
@login_required
def equipo_baja(equipo_id):
    """Baja lógica. No se borra: el histórico de inspecciones lo referencia,
    y un equipo retirado tiene que seguir explicando las visitas viejas."""
    _solo_gestion()
    obj = db.session.get(Equipo, equipo_id)
    if obj is None:
        abort(404)
    inst = obj.instalacion
    _verificar_empresa(inst.cliente.empresa_id)

    obj.activo = False
    for hijo in obj.hijos:
        hijo.activo = False
    db.session.commit()
    flash(f"«{obj.etiqueta}» dado de baja. El histórico de sus inspecciones se conserva.", "ok")
    return redirect(url_for("principal.instalacion", instalacion_id=inst.id))


# ---------------------------------------------------------------------------
# Visitas
# ---------------------------------------------------------------------------


@principal.route("/visitas")
@login_required
def visitas():
    query = _visitas_empresa()
    # Un técnico ve lo suyo; gestión ve todo.
    if current_user.rol == "Técnico":
        query = query.filter(Visita.tecnico_id == current_user.id)
    lista = query.order_by(Visita.fecha.desc(), Visita.id.desc()).limit(60).all()
    return render_template("visitas.html", visitas=lista)


@principal.route("/visitas/exportar")
@login_required
def visitas_exportar():
    query = _visitas_empresa()
    if current_user.rol == "Técnico":
        query = query.filter(Visita.tecnico_id == current_user.id)
    visitas_lista = query.order_by(Visita.fecha.desc()).all()

    filas = [
        [
            v.id, v.fecha.strftime("%d/%m/%Y"), v.instalacion.cliente.nombre, v.instalacion.nombre,
            v.tecnico.nombre_completo if v.tecnico else "",
            ", ".join(f"{i.categoria.nombre} ({i.rutina})" for i in v.items),
            "Sí" if v.firmada else "No",
        ]
        for v in visitas_lista
    ]
    return csv_response(
        "visitas.csv",
        ["N°", "Fecha", "Cliente", "Instalación", "Técnico", "Categorías", "Firmada"],
        filas,
    )


@principal.route("/visita/<int:visita_id>")
@login_required
def visita(visita_id):
    obj = db.session.get(Visita, visita_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)

    observaciones = (
        Observacion.query.filter_by(visita_id=obj.id).order_by(Observacion.id).all()
    )
    return render_template("visita.html", visita=obj, observaciones=observaciones)


@principal.route("/visita/<int:visita_id>/informe.pdf")
@login_required
def visita_informe(visita_id):
    obj = db.session.get(Visita, visita_id)
    if obj is None:
        abort(404)
    if current_user.es_cliente:
        if current_user.cliente_id != obj.instalacion.cliente_id:
            abort(403)
    else:
        _verificar_empresa(obj.instalacion.cliente.empresa_id)

    pdf_bytes = generar_informe_visita(current_app, obj)
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="informe-visita-{obj.id}.pdf"'},
    )


@principal.route("/visita/<int:visita_id>/firmar", methods=["POST"])
@login_required
def visita_firmar(visita_id):
    obj = db.session.get(Visita, visita_id)
    if obj is None:
        abort(404)
    empresa_id = obj.instalacion.cliente.empresa_id
    _verificar_empresa(empresa_id)

    cambio = False
    for campo, form_key in (("firma_tecnico_archivo", "firma_tecnico"), ("firma_cliente_archivo", "firma_cliente")):
        data_url = request.form.get(form_key)
        if not data_url:
            continue
        try:
            setattr(obj, campo, guardar_firma(current_app, data_url, empresa_id))
            cambio = True
        except FotoInvalida as exc:
            flash(str(exc), "error")
            return redirect(url_for("principal.visita", visita_id=obj.id))

    nombre_cliente = (request.form.get("firma_cliente_nombre") or "").strip()
    if nombre_cliente:
        obj.firma_cliente_nombre = nombre_cliente
        # El nombre solo no cuenta como firma: sin trazo dibujado no hay
        # nada que mostrar en el informe.

    if not cambio:
        flash("No había ninguna firma nueva para guardar.", "error")
        return redirect(url_for("principal.visita", visita_id=obj.id))

    obj.fecha_firma = datetime.utcnow()
    db.session.commit()
    flash("Firma guardada.", "ok")
    return redirect(url_for("principal.visita", visita_id=obj.id))


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------


@principal.route("/checklist/<int:item_id>", methods=["GET", "POST"])
@login_required
def checklist(item_id):
    item = db.session.get(ItemVisita, item_id)
    if item is None:
        abort(404)
    _verificar_empresa(item.visita.instalacion.cliente.empresa_id)

    if request.method == "POST":
        resultado = guardar_checklist(item, request.form, current_user)
        if resultado.ok:
            partes = [f"{resultado.respuestas} punto(s) guardado(s)"]
            if resultado.observaciones:
                estado = "aprobada(s)" if current_user.puede_aprobar else "pendiente(s) de aprobación"
                partes.append(f"{resultado.observaciones} deficiencia(s) {estado}")
            flash(" · ".join(partes), "ok")
            # Cargar el checklist pone la OT en curso sola: el técnico no
            # tiene que acordarse de cambiarle el estado a mano.
            orden_asociada = item.visita.orden
            if orden_asociada and orden_asociada.estado == OT_PENDIENTE:
                orden_asociada.estado = OT_EN_CURSO
                db.session.commit()
            if orden_asociada:
                return redirect(url_for("principal.orden", orden_id=orden_asociada.id))
            return redirect(url_for("principal.visita", visita_id=item.visita_id))
        for error in resultado.errores:
            flash(error, "error")

    bloques = armar_bloques(item)
    # Equipos que aparecen en esta inspección: es entre esos que el técnico
    # elige a cuál corresponde cada foto.
    equipos_foto, vistos = [], set()
    for bloque in bloques:
        for seccion in bloque.secciones:
            eq = seccion.equipo
            if eq and eq.id not in vistos:
                vistos.add(eq.id)
                equipos_foto.append(eq)
    return render_template(
        "checklist.html", item=item, bloques=bloques, equipos_foto=equipos_foto
    )


# ---------------------------------------------------------------------------
# Banco de fotos
# ---------------------------------------------------------------------------


def fotos_del_item(item):
    """Las fotos ya cargadas en esa inspección, para repintarlas."""
    return Foto.query.filter_by(item_visita_id=item.id).order_by(Foto.id).all()


principal.add_app_template_global(fotos_del_item, "fotos_del_item")
principal.add_app_template_global(ruta_relativa, "ruta_foto")


@principal.route("/foto/subir", methods=["POST"])
@login_required
def subir_foto():
    """Sube una foto sola, apenas se elige.

    No viaja con el formulario del checklist: una rutina anual con quince
    fotos serían decenas de MB en un solo POST, y si la validación falla se
    pierden todas. Devuelve JSON para que la pantalla muestre la miniatura
    sin recargar.
    """
    item = db.session.get(ItemVisita, request.form.get("item_id", type=int) or 0)
    if item is None:
        return jsonify(ok=False, error="Ítem de visita inexistente."), 404
    instalacion = item.visita.instalacion
    if instalacion.cliente.empresa_id != current_user.empresa_id:
        return jsonify(ok=False, error="Sin acceso."), 403

    equipo = None
    equipo_id = request.form.get("equipo_id", type=int)
    if equipo_id:
        equipo = db.session.get(Equipo, equipo_id)
        if equipo is None or equipo.instalacion_id != instalacion.id:
            return jsonify(ok=False, error="El equipo no es de esta instalación."), 400

    try:
        nombre, ancho, alto, peso = guardar_archivo(
            current_app, request.files.get("foto"), current_user.empresa_id
        )
    except FotoInvalida as error:
        return jsonify(ok=False, error=str(error)), 400

    foto = Foto(
        instalacion_id=instalacion.id,
        equipo_id=equipo.id if equipo else None,
        item_visita_id=item.id,
        descripcion=(request.form.get("nota") or "").strip()[:300] or None,
        archivo=nombre,
        nombre_original=(request.files["foto"].filename or "")[:255],
        ancho=ancho, alto=alto, bytes=peso,
        tomada_por_id=current_user.id,
    )
    db.session.add(foto)
    db.session.commit()

    return jsonify(
        ok=True, id=foto.id,
        equipo=equipo.etiqueta if equipo else "sin equipo",
        nota=foto.descripcion or "",
        url=url_for("static", filename=ruta_relativa(current_user.empresa_id, nombre)),
        borrar=url_for("principal.foto_borrar", foto_id=foto.id),
    )


@principal.route("/foto/<int:foto_id>/borrar", methods=["POST"])
@login_required
def foto_borrar(foto_id):
    foto = db.session.get(Foto, foto_id)
    if foto is None:
        return jsonify(ok=False, error="No existe."), 404
    if foto.instalacion.cliente.empresa_id != current_user.empresa_id:
        return jsonify(ok=False, error="Sin acceso."), 403

    borrar_archivo(current_app, current_user.empresa_id, foto.archivo)
    db.session.delete(foto)
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    flash("Foto eliminada.", "ok")
    return redirect(request.referrer or url_for("principal.banco_fotos"))


# ---------------------------------------------------------------------------
# Prueba anual de caudal
# ---------------------------------------------------------------------------


def _serializar_ensayo(ensayo):
    if ensayo is None:
        return None
    return {
        "metodo": ensayo.metodo,
        "curva_conforme": ensayo.curva_conforme,
        "comentario": ensayo.comentario,
        "puntos": [
            {
                "etiqueta": p.etiqueta, "caudal": p.caudal, "succion": p.succion,
                "descarga": p.descarga, "rpm": p.rpm,
            }
            for p in ensayo.puntos
        ],
    }


@principal.route("/checklist/<int:item_id>/ensayo/<int:equipo_id>", methods=["GET", "POST"])
@login_required
def ensayo_caudal(item_id, equipo_id):
    """Carga y guarda la prueba anual de caudal de un equipo.

    Aparte del resto del checklist porque no es una grilla de campos: es
    un ensayo con puntos dinámicos que se recalculan en el navegador
    (succión, corrección por afinidad) y se reevalúan acá al guardar,
    contra la placa VIGENTE del equipo — no contra la que había cuando
    se cargó, para no arrastrar un resultado desincronizado si alguien
    corrige la placa después.
    """
    item = db.session.get(ItemVisita, item_id)
    if item is None:
        return jsonify(ok=False, error="Ítem de visita inexistente."), 404
    instalacion = item.visita.instalacion
    if instalacion.cliente.empresa_id != current_user.empresa_id:
        return jsonify(ok=False, error="Sin acceso."), 403

    equipo = db.session.get(Equipo, equipo_id)
    if equipo is None or equipo.instalacion_id != instalacion.id:
        return jsonify(ok=False, error="El equipo no es de esta instalación."), 400

    tipo = TipoFormulario.query.filter_by(
        empresa_id=current_user.empresa_id,
        categoria_id=item.categoria_id,
        tipo_equipo_id=equipo.tipo_equipo_id,
        es_ensayo_curva=True,
    ).first()
    if tipo is None:
        return jsonify(ok=False, error="Este equipo no tiene prueba de caudal en el catálogo."), 400

    ensayo = EnsayoCaudal.query.filter_by(item_visita_id=item.id, equipo_id=equipo.id).first()

    if request.method == "GET":
        return jsonify(ok=True, ensayo=_serializar_ensayo(ensayo))

    datos = request.get_json(silent=True) or {}
    if ensayo is None:
        ensayo = EnsayoCaudal(item_visita_id=item.id, tipo_formulario_id=tipo.id, equipo_id=equipo.id)
        db.session.add(ensayo)
    ensayo.metodo = (datos.get("metodo") or "").strip() or None
    ensayo.curva_conforme = datos.get("curva_conforme")
    ensayo.comentario = (datos.get("comentario") or "").strip() or None
    ensayo.creado_por_id = current_user.id
    db.session.flush()

    # Los 4 puntos fijos (churn/50/100/150) se actualizan EN LA MISMA
    # fila, no se borran y recrean: son los únicos que pueden tener una
    # deficiencia abierta (ver actualizar_observacion), y esa deficiencia
    # se encuentra por punto_ensayo_id. Borrar y recrear le cambiaría el
    # id al punto en cada guardado, la función nunca encontraría la
    # deficiencia existente y terminaría abriendo una nueva cada vez.
    # Los extras no tienen ese problema — nunca cargan una deficiencia —
    # así que esos sí se reemplazan enteros, es más simple.
    existentes_fijos = {p.etiqueta: p for p in ensayo.puntos if p.etiqueta in PUNTOS_FIJOS}
    for punto_extra in [p for p in ensayo.puntos if p.etiqueta not in PUNTOS_FIJOS]:
        db.session.delete(punto_extra)
    db.session.flush()

    aprueba_solo = bool(current_user.puede_aprobar)
    resultados = []
    for indice, dato_punto in enumerate(datos.get("puntos") or []):
        etiqueta = (dato_punto.get("etiqueta") or "extra")[:20]
        punto = existentes_fijos.get(etiqueta) if etiqueta in PUNTOS_FIJOS else None
        if punto is None:
            punto = PuntoEnsayoCaudal(ensayo_id=ensayo.id, etiqueta=etiqueta)
            db.session.add(punto)
        punto.orden = indice
        punto.caudal = dato_punto.get("caudal")
        punto.succion = dato_punto.get("succion")
        punto.descarga = dato_punto.get("descarga")
        punto.rpm = dato_punto.get("rpm")
        db.session.flush()
        resultado = evaluar_punto(equipo, punto)
        actualizar_observacion(punto, resultado, equipo, instalacion, item, current_user, aprueba_solo)
        resultados.append({"etiqueta": punto.etiqueta, **(resultado or {})})

    # Cargar el ensayo pone la OT en curso, igual que el resto del
    # checklist (ver checklist.py: guardar_checklist).
    orden_asociada = item.visita.orden
    if orden_asociada and orden_asociada.estado == OT_PENDIENTE:
        orden_asociada.estado = OT_EN_CURSO

    db.session.commit()
    return jsonify(ok=True, resultados=resultados)


@principal.route("/fotos")
@login_required
def banco_fotos():
    """El banco, navegable por tipo de equipo."""
    query = (
        Foto.query.join(Instalacion, Foto.instalacion_id == Instalacion.id)
        .join(Cliente, Instalacion.cliente_id == Cliente.id)
        .filter(Cliente.empresa_id == current_user.empresa_id)
    )

    tipo_id = request.args.get("tipo", type=int)
    if tipo_id:
        query = query.join(Equipo, Foto.equipo_id == Equipo.id).filter(
            Equipo.tipo_equipo_id == tipo_id
        )
    cliente_id = request.args.get("cliente", type=int)
    if cliente_id:
        query = query.filter(Cliente.id == cliente_id)

    lista = query.order_by(Foto.fecha.desc(), Foto.id.desc()).limit(200).all()

    # Solo los tipos que de verdad tienen fotos: un filtro con opciones
    # vacías es ruido.
    con_fotos = {f.equipo.tipo_equipo_id for f in query.all() if f.equipo}
    tipos = (
        TipoEquipo.query.filter(TipoEquipo.id.in_(con_fotos))
        .order_by(TipoEquipo.categoria_id, TipoEquipo.orden).all()
        if con_fotos else []
    )
    clientes_lista = (
        Cliente.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(Cliente.nombre).all()
    )

    return render_template(
        "fotos.html", fotos=lista, tipos=tipos, clientes=clientes_lista,
        tipo_id=tipo_id, cliente_id=cliente_id,
    )


# ---------------------------------------------------------------------------
# Banco de deficiencias
# ---------------------------------------------------------------------------


@principal.route("/deficiencias")
@principal.route("/deficiencias/<int:cliente_id>")
@login_required
def deficiencias(cliente_id=None):
    query = _observaciones_empresa()
    if cliente_id:
        query = query.filter(Cliente.id == cliente_id)

    filtro = request.args.get("estado")
    if filtro == "pendientes":
        query = query.filter(Observacion.estado_revision == REVISION_PENDIENTE)
    elif filtro == "criticas":
        query = query.filter(Observacion.clasificacion == CLASIF_CRITICA)

    observaciones = query.order_by(
        Observacion.estado_revision.desc(),
        Observacion.fecha_carga.desc(),
        Observacion.id.desc(),
    ).all()

    lista_clientes = (
        Cliente.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(Cliente.nombre).all()
    )
    return render_template(
        "deficiencias.html", observaciones=observaciones,
        clientes=lista_clientes, cliente_id=cliente_id, filtro=filtro,
    )


@principal.route("/deficiencias/exportar")
@principal.route("/deficiencias/<int:cliente_id>/exportar")
@login_required
def deficiencias_exportar(cliente_id=None):
    query = _observaciones_empresa()
    if cliente_id:
        query = query.filter(Cliente.id == cliente_id)
    filtro = request.args.get("estado")
    if filtro == "pendientes":
        query = query.filter(Observacion.estado_revision == REVISION_PENDIENTE)
    elif filtro == "criticas":
        query = query.filter(Observacion.clasificacion == CLASIF_CRITICA)

    observaciones = query.order_by(Observacion.fecha_carga.desc()).all()
    filas = [
        [
            o.clasificacion, o.descripcion, o.instalacion.cliente.nombre, o.instalacion.nombre,
            o.equipo.etiqueta if o.equipo else "", o.fecha_carga.strftime("%d/%m/%Y"),
            o.estado_revision, "Sí" if o.resuelto else "No",
        ]
        for o in observaciones
    ]
    return csv_response(
        "deficiencias.csv",
        ["Clase", "Descripción", "Cliente", "Instalación", "Equipo", "Fecha", "Estado revisión", "Resuelta"],
        filas,
    )


@principal.route("/observacion/<int:observacion_id>/aprobar", methods=["POST"])
@login_required
def aprobar(observacion_id):
    obj = db.session.get(Observacion, observacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)
    if not current_user.puede_aprobar:
        abort(403)

    obj.aprobar(current_user)
    if obj.creado_por_id:
        notificar_usuario(
            obj.creado_por, "observacion_aprobada", f"«{obj.descripcion[:80]}» fue aprobada.",
            current_user.empresa_id,
            enlace=url_for("principal.instalacion", instalacion_id=obj.instalacion_id),
            remitente=current_user,
        )
    db.session.commit()
    flash("Deficiencia aprobada — ya la ve el cliente.", "ok")
    return redirect(request.referrer or url_for("principal.deficiencias"))


@principal.route("/observacion/<int:observacion_id>/resolver", methods=["POST"])
@login_required
def resolver(observacion_id):
    obj = db.session.get(Observacion, observacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)
    if not current_user.puede_aprobar:
        abort(403)

    obj.resuelto = True
    obj.fecha_resolucion = date.today()
    db.session.commit()
    flash("Deficiencia marcada como resuelta.", "ok")
    return redirect(request.referrer or url_for("principal.deficiencias"))


@principal.route("/observacion/<int:observacion_id>/presupuesto", methods=["POST"])
@login_required
def presupuesto_solicitar(observacion_id):
    _solo_gestion()
    obj = db.session.get(Observacion, observacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.instalacion.cliente.empresa_id)
    if obj.presupuesto is not None:
        flash("Esta deficiencia ya tiene un presupuesto asociado.", "error")
        return redirect(request.referrer or url_for("principal.deficiencias"))

    presupuesto = crear_presupuesto(obj, current_user)
    db.session.commit()
    flash(f"{presupuesto.codigo} creado.", "ok")
    return redirect(url_for("principal.presupuesto", presupuesto_id=presupuesto.id))


# ---------------------------------------------------------------------------
# Presupuestos
# ---------------------------------------------------------------------------


def _presupuestos_empresa():
    return Presupuesto.query.filter_by(empresa_id=current_user.empresa_id)


@principal.route("/presupuestos")
@login_required
def presupuestos():
    _solo_gestion()
    filtro = request.args.get("estado")
    query = _presupuestos_empresa()
    if filtro in ESTADOS_PRESUPUESTO:
        lista = query.filter(Presupuesto.estado == filtro).order_by(Presupuesto.fecha_creacion.desc()).all()
    else:
        lista = query.order_by(Presupuesto.fecha_creacion.desc()).all()

    por_estado = {e: [p for p in lista if p.estado == e] for e in ESTADOS_PRESUPUESTO} if not filtro else None
    return render_template(
        "presupuestos.html", presupuestos=lista, por_estado=por_estado, filtro=filtro,
    )


@principal.route("/presupuesto/<int:presupuesto_id>", methods=["GET", "POST"])
@login_required
def presupuesto(presupuesto_id):
    _solo_gestion()
    obj = db.session.get(Presupuesto, presupuesto_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)

    if request.method == "POST":
        nuevo = request.form.get("estado", "")
        nota = (request.form.get("nota") or "").strip() or None
        try:
            obj.cambiar_estado(nuevo, current_user, nota)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("principal.presupuesto", presupuesto_id=obj.id))
        notificar_gestion(
            current_user.empresa_id, "presupuesto", f"{obj.codigo} → {nuevo}.",
            enlace=url_for("principal.presupuesto", presupuesto_id=obj.id), remitente=current_user,
        )
        db.session.commit()
        flash(f"{obj.codigo} → {nuevo}.", "ok")
        return redirect(url_for("principal.presupuesto", presupuesto_id=obj.id))

    return render_template("presupuesto.html", presupuesto=obj)


@principal.route("/presupuesto/<int:presupuesto_id>/eliminar", methods=["POST"])
@login_required
def presupuesto_eliminar(presupuesto_id):
    _solo_gestion()
    obj = db.session.get(Presupuesto, presupuesto_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)
    db.session.delete(obj)
    db.session.commit()
    flash("Presupuesto eliminado.", "ok")
    return redirect(url_for("principal.presupuestos"))


# ---------------------------------------------------------------------------
# Inventario de repuestos
# ---------------------------------------------------------------------------


def _repuestos_empresa():
    return Repuesto.query.filter_by(empresa_id=current_user.empresa_id, activo=True)


@principal.route("/repuestos")
@login_required
def repuestos():
    lista = _repuestos_empresa().order_by(Repuesto.nombre).all()
    return render_template("repuestos.html", repuestos=lista, solo_criticos=False)


@principal.route("/repuestos/criticos")
@login_required
def repuestos_criticos_lista():
    lista = repuestos_criticos(current_user.empresa_id)
    return render_template("repuestos.html", repuestos=lista, solo_criticos=True)


@principal.route("/repuesto/nuevo", methods=["GET", "POST"])
@principal.route("/repuesto/<int:repuesto_id>/editar", methods=["GET", "POST"])
@login_required
def repuesto_form(repuesto_id=None):
    _solo_gestion()
    obj = db.session.get(Repuesto, repuesto_id) if repuesto_id else Repuesto(empresa_id=current_user.empresa_id)
    if repuesto_id and obj is None:
        abort(404)
    if repuesto_id:
        _verificar_empresa(obj.empresa_id)

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre es obligatorio.", "error")
            return render_template("repuesto_form.html", repuesto=obj)
        obj.nombre = nombre
        obj.codigo = (request.form.get("codigo") or "").strip() or None
        obj.unidad = (request.form.get("unidad") or "unidad").strip()
        obj.stock_minimo = max(0, request.form.get("stock_minimo", type=int) or 0)
        if repuesto_id is None:
            obj.stock_actual = max(0, request.form.get("stock_actual", type=int) or 0)
        obj.activo = bool(request.form.get("activo")) if repuesto_id else True
        if repuesto_id is None:
            db.session.add(obj)
        db.session.commit()
        flash(f"«{obj.nombre}» guardado.", "ok")
        return redirect(url_for("principal.repuestos"))

    return render_template("repuesto_form.html", repuesto=obj if repuesto_id else None)


@principal.route("/repuesto/<int:repuesto_id>/reponer", methods=["POST"])
@login_required
def repuesto_reponer(repuesto_id):
    obj = db.session.get(Repuesto, repuesto_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)
    cantidad = request.form.get("cantidad", type=int) or 0
    try:
        reponer_stock(obj, cantidad)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("principal.repuestos"))
    db.session.commit()
    flash(f"Stock de «{obj.nombre}» actualizado a {obj.stock_actual}.", "ok")
    return redirect(request.referrer or url_for("principal.repuestos"))


@principal.route("/repuesto/<int:repuesto_id>/eliminar", methods=["POST"])
@login_required
def repuesto_eliminar(repuesto_id):
    _solo_gestion()
    obj = db.session.get(Repuesto, repuesto_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)
    if ConsumoRepuesto.query.filter_by(repuesto_id=obj.id).first():
        obj.activo = False
        db.session.commit()
        flash(f"«{obj.nombre}» tiene consumos registrados: se desactivó en vez de borrarse.", "ok")
    else:
        db.session.delete(obj)
        db.session.commit()
        flash(f"«{obj.nombre}» eliminado.", "ok")
    return redirect(url_for("principal.repuestos"))


@principal.route("/orden/<int:orden_id>/repuesto/consumir", methods=["POST"])
@login_required
def orden_repuesto_consumir(orden_id):
    obj = db.session.get(OrdenTrabajo, orden_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.visita.instalacion.cliente.empresa_id)
    repuesto = db.session.get(Repuesto, request.form.get("repuesto_id", type=int) or 0)
    if repuesto is None or repuesto.empresa_id != current_user.empresa_id:
        abort(404)
    cantidad = request.form.get("cantidad", type=int) or 0

    try:
        registrar_consumo(obj, repuesto, cantidad)
    except (ValueError, StockInsuficiente) as exc:
        flash(str(exc), "error")
        return redirect(url_for("principal.orden", orden_id=obj.id))

    if repuesto.en_nivel_critico:
        notificar_gestion(
            current_user.empresa_id, "stock_critico",
            f"«{repuesto.nombre}» quedó en {repuesto.stock_actual} {repuesto.unidad}(s).",
            enlace=url_for("principal.repuestos_criticos_lista"), remitente=current_user,
        )
    db.session.commit()
    flash(f"{cantidad} {repuesto.unidad}(s) de «{repuesto.nombre}» descontado(s).", "ok")
    return redirect(url_for("principal.orden", orden_id=obj.id))


# ---------------------------------------------------------------------------
# Notificaciones internas
# ---------------------------------------------------------------------------


@principal.route("/notificaciones")
@login_required
def notificaciones():
    no_leidas = (
        Notificacion.query.filter_by(destinatario_id=current_user.id, leido=False)
        .order_by(Notificacion.fecha_carga.desc()).limit(150).all()
    )
    leidas = (
        Notificacion.query.filter_by(destinatario_id=current_user.id, leido=True)
        .order_by(Notificacion.fecha_carga.desc()).limit(20).all()
    )
    return render_template("notificaciones.html", no_leidas=no_leidas, leidas=leidas)


@principal.route("/notificaciones/resumen")
@login_required
def notificaciones_resumen():
    no_leidas = (
        Notificacion.query.filter_by(destinatario_id=current_user.id, leido=False)
        .order_by(Notificacion.fecha_carga.desc()).limit(20).all()
    )
    return render_template("_notificaciones_resumen.html", no_leidas=no_leidas)


@principal.route("/notificacion/<int:notificacion_id>/ir")
@login_required
def notificacion_ir(notificacion_id):
    obj = db.session.get(Notificacion, notificacion_id)
    if obj is None or obj.destinatario_id != current_user.id:
        abort(404)
    obj.leido = True
    db.session.commit()
    return redirect(obj.enlace or url_for("principal.notificaciones"))


@principal.route("/notificaciones/marcar-leidas", methods=["POST"])
@login_required
def notificaciones_marcar_leidas():
    ids = request.form.getlist("id", type=int)
    (
        Notificacion.query.filter(
            Notificacion.id.in_(ids), Notificacion.destinatario_id == current_user.id
        ).update({"leido": True}, synchronize_session=False)
    )
    db.session.commit()
    return redirect(request.referrer or url_for("principal.notificaciones"))


@principal.route("/notificaciones/marcar-todas-leidas", methods=["POST"])
@login_required
def notificaciones_marcar_todas():
    Notificacion.query.filter_by(destinatario_id=current_user.id, leido=False).update(
        {"leido": True}, synchronize_session=False
    )
    db.session.commit()
    return redirect(request.referrer or url_for("principal.notificaciones"))


# ---------------------------------------------------------------------------
# Usuarios y datos de la empresa
# ---------------------------------------------------------------------------


@principal.route("/usuarios")
@login_required
def usuarios():
    _solo_gestion()
    lista = (
        Usuario.query.filter_by(empresa_id=current_user.empresa_id, cliente_id=None)
        .order_by(Usuario.activo.desc(), Usuario.nombre_completo).all()
    )
    return render_template("usuarios.html", usuarios=lista)


@principal.route("/usuario/nuevo", methods=["GET", "POST"])
@principal.route("/usuario/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
def usuario_form(usuario_id=None):
    _solo_gestion()
    obj = db.session.get(Usuario, usuario_id) if usuario_id else None
    if usuario_id and obj is None:
        abort(404)
    if usuario_id:
        _verificar_empresa(obj.empresa_id)

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        nombre_completo = (request.form.get("nombre_completo") or "").strip()
        rol = request.form.get("rol")
        if not username or not nombre_completo:
            flash("Usuario y nombre completo son obligatorios.", "error")
            return render_template("usuario_form.html", usuario=obj)
        if rol not in ROLES or rol == "Cliente":
            flash("Rol inválido.", "error")
            return render_template("usuario_form.html", usuario=obj)

        existente = Usuario.query.filter(
            Usuario.username == username, Usuario.id != (obj.id if obj else -1)
        ).first()
        if existente:
            flash(f"Ya existe un usuario con el username «{username}».", "error")
            return render_template("usuario_form.html", usuario=obj)

        password = request.form.get("password") or ""
        if obj is None and len(password) < 4:
            flash("La contraseña debe tener al menos 4 caracteres.", "error")
            return render_template("usuario_form.html", usuario=obj)

        if obj is None:
            obj = Usuario(empresa_id=current_user.empresa_id)
            obj.set_password(password)
            db.session.add(obj)
        elif password:
            obj.set_password(password)

        obj.username = username
        obj.nombre_completo = nombre_completo
        obj.rol = rol
        obj.activo = bool(request.form.get("activo")) if usuario_id else True
        db.session.commit()
        flash(f"«{obj.nombre_completo}» guardado.", "ok")
        return redirect(url_for("principal.usuarios"))

    return render_template("usuario_form.html", usuario=obj)


@principal.route("/usuario/<int:usuario_id>/baja", methods=["POST"])
@login_required
def usuario_baja(usuario_id):
    _solo_gestion()
    obj = db.session.get(Usuario, usuario_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)
    if obj.id == current_user.id:
        flash("No podés darte de baja a vos mismo.", "error")
        return redirect(url_for("principal.usuarios"))
    obj.activo = False
    db.session.commit()
    flash(f"«{obj.nombre_completo}» dado de baja.", "ok")
    return redirect(url_for("principal.usuarios"))


@principal.route("/usuario/<int:usuario_id>/habilitacion/nueva", methods=["POST"])
@login_required
def habilitacion_nueva(usuario_id):
    _solo_gestion()
    usuario_obj = db.session.get(Usuario, usuario_id)
    if usuario_obj is None:
        abort(404)
    _verificar_empresa(usuario_obj.empresa_id)

    nombre = (request.form.get("nombre") or "").strip()
    if not nombre:
        flash("El nombre de la habilitación es obligatorio.", "error")
        return redirect(url_for("principal.usuario_form", usuario_id=usuario_id))

    db.session.add(HabilitacionTecnico(
        usuario_id=usuario_id, nombre=nombre,
        vencimiento=_fecha(request.form.get("vencimiento")),
        nota=(request.form.get("nota") or "").strip() or None,
    ))
    db.session.commit()
    flash("Habilitación agregada.", "ok")
    return redirect(url_for("principal.usuario_form", usuario_id=usuario_id))


@principal.route("/habilitacion/<int:habilitacion_id>/eliminar", methods=["POST"])
@login_required
def habilitacion_eliminar(habilitacion_id):
    _solo_gestion()
    obj = db.session.get(HabilitacionTecnico, habilitacion_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.usuario.empresa_id)
    usuario_id = obj.usuario_id
    db.session.delete(obj)
    db.session.commit()
    flash("Habilitación eliminada.", "ok")
    return redirect(url_for("principal.usuario_form", usuario_id=usuario_id))


@principal.route("/empresa/editar", methods=["GET", "POST"])
@login_required
def empresa_editar():
    _solo_gestion()
    obj = current_user.empresa

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre de la empresa es obligatorio.", "error")
            return render_template("empresa_form.html", empresa=obj)
        obj.nombre = nombre
        obj.rut = (request.form.get("rut") or "").strip() or None

        archivo = request.files.get("logo")
        if archivo and archivo.filename:
            try:
                nombre_archivo, _, _, _ = guardar_archivo(current_app, archivo, obj.id)
                obj.logo = ruta_relativa(obj.id, nombre_archivo)
            except FotoInvalida as exc:
                flash(str(exc), "error")
                return render_template("empresa_form.html", empresa=obj)

        db.session.commit()
        flash("Datos de la empresa actualizados.", "ok")
        return redirect(url_for("principal.empresa_editar"))

    return render_template("empresa_form.html", empresa=obj)


# ---------------------------------------------------------------------------
# Calendario de visitas
# ---------------------------------------------------------------------------


@principal.route("/calendario")
@login_required
def calendario():
    hoy = date.today()
    anio = request.args.get("anio", type=int) or hoy.year
    mes = request.args.get("mes", type=int) or hoy.month
    mes = min(12, max(1, mes))
    tecnico_id = request.args.get("tecnico_id", type=int)

    primero = date(anio, mes, 1)
    ultimo = date(anio, mes, monthrange(anio, mes)[1])

    query = _visitas_empresa().filter(Visita.fecha >= primero, Visita.fecha <= ultimo)
    if current_user.rol == "Técnico":
        query = query.filter(Visita.tecnico_id == current_user.id)
    elif tecnico_id:
        query = query.filter(Visita.tecnico_id == tecnico_id)
    visitas_mes = query.order_by(Visita.fecha).all()

    por_dia = {}
    for v in visitas_mes:
        por_dia.setdefault(v.fecha.day, []).append(v)

    # Grilla de semanas: None donde el mes no tiene día (relleno antes/después).
    primer_dow = primero.weekday()  # 0 = lunes
    dias_mes = ultimo.day
    celdas = [None] * primer_dow + list(range(1, dias_mes + 1))
    while len(celdas) % 7:
        celdas.append(None)
    semanas = [celdas[i:i + 7] for i in range(0, len(celdas), 7)]

    tecnicos = (
        Usuario.query.filter_by(empresa_id=current_user.empresa_id, activo=True)
        .filter(Usuario.rol.in_(("Técnico", "Jefe técnico")))
        .order_by(Usuario.nombre_completo).all()
        if not current_user.rol == "Técnico" else []
    )

    return render_template(
        "calendario.html", semanas=semanas, por_dia=por_dia, anio=anio, mes=mes,
        MESES=MESES, hoy=hoy, tecnicos=tecnicos, tecnico_id=tecnico_id,
    )


# ---------------------------------------------------------------------------
# Catálogo de la empresa
# ---------------------------------------------------------------------------


@principal.route("/catalogo")
@login_required
def catalogo():
    """Lo que define qué se inspecciona. Vive a nivel empresa: se carga una
    vez y sirve para todos los clientes."""
    if not current_user.puede_aprobar:
        abort(403)

    categorias = (
        CategoriaEquipo.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(CategoriaEquipo.orden).all()
    )
    tipos_equipo = (
        TipoEquipo.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(TipoEquipo.categoria_id, TipoEquipo.orden).all()
    )
    formularios = (
        TipoFormulario.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(TipoFormulario.categoria_id, TipoFormulario.orden).all()
    )
    return render_template(
        "catalogo.html", categorias=categorias,
        tipos_equipo=tipos_equipo, formularios=formularios,
    )


@principal.route("/catalogo/categoria/nueva", methods=["GET", "POST"])
@principal.route("/catalogo/categoria/<int:categoria_id>", methods=["GET", "POST"])
@login_required
def categoria_form(categoria_id=None):
    _solo_gestion()
    obj = None
    if categoria_id:
        obj = db.session.get(CategoriaEquipo, categoria_id)
        if obj is None:
            abort(404)
        _verificar_empresa(obj.empresa_id)

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("La categoría necesita un nombre.", "error")
            return render_template("categoria_form.html", categoria=obj)
        if obj is None:
            obj = CategoriaEquipo(empresa_id=current_user.empresa_id)
            db.session.add(obj)
        obj.nombre = nombre
        obj.orden = request.form.get("orden", type=int) or 0
        db.session.commit()
        flash("Categoría guardada.", "ok")
        return redirect(url_for("principal.catalogo"))

    return render_template("categoria_form.html", categoria=obj)


@principal.route("/catalogo/tipo-equipo/nuevo", methods=["GET", "POST"])
@principal.route("/catalogo/tipo-equipo/<int:tipo_id>", methods=["GET", "POST"])
@login_required
def tipo_equipo_form(tipo_id=None):
    _solo_gestion()
    obj = None
    if tipo_id:
        obj = db.session.get(TipoEquipo, tipo_id)
        if obj is None:
            abort(404)
        _verificar_empresa(obj.empresa_id)

    categorias = categorias_disponibles(current_user.empresa_id)
    # Un tipo no puede colgar de sí mismo.
    posibles_padres = (
        TipoEquipo.query.filter_by(empresa_id=current_user.empresa_id)
        .filter(TipoEquipo.id != (obj.id if obj else 0))
        .order_by(TipoEquipo.categoria_id, TipoEquipo.orden).all()
    )

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El tipo de equipo necesita un nombre.", "error")
            return render_template("tipo_equipo_form.html", tipo=obj,
                                   categorias=categorias, padres=posibles_padres)
        if obj is None:
            obj = TipoEquipo(empresa_id=current_user.empresa_id)
            db.session.add(obj)
        obj.nombre = nombre
        obj.categoria_id = request.form.get("categoria_id", type=int) or None
        obj.tipo_padre_id = request.form.get("tipo_padre_id", type=int) or None
        obj.encabeza_conjunto = bool(request.form.get("encabeza_conjunto"))
        obj.orden = request.form.get("orden", type=int) or 0
        db.session.commit()
        flash("Tipo de equipo guardado.", "ok")
        return redirect(url_for("principal.catalogo"))

    return render_template("tipo_equipo_form.html", tipo=obj,
                           categorias=categorias, padres=posibles_padres)


@principal.route("/catalogo/formulario/nuevo", methods=["GET", "POST"])
@principal.route("/catalogo/formulario/<int:formulario_id>", methods=["GET", "POST"])
@login_required
def formulario_form(formulario_id=None):
    """Metadatos del formulario y sus campos, en una sola pantalla.

    Separarlos obligaría a guardar dos veces para dar de alta un checklist,
    y a navegar entre pantallas para algo que se piensa junto.
    """
    _solo_gestion()
    obj = None
    if formulario_id:
        obj = db.session.get(TipoFormulario, formulario_id)
        if obj is None:
            abort(404)
        _verificar_empresa(obj.empresa_id)

    categorias = categorias_disponibles(current_user.empresa_id)
    tipos_equipo = (
        TipoEquipo.query.filter_by(empresa_id=current_user.empresa_id)
        .order_by(TipoEquipo.categoria_id, TipoEquipo.orden).all()
    )

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El formulario necesita un nombre.", "error")
            return render_template("formulario_form.html", formulario=obj,
                                   categorias=categorias, tipos_equipo=tipos_equipo)
        if obj is None:
            obj = TipoFormulario(empresa_id=current_user.empresa_id)
            db.session.add(obj)

        obj.nombre = nombre
        obj.descripcion = (request.form.get("descripcion") or "").strip() or None
        obj.categoria_id = request.form.get("categoria_id", type=int) or None
        obj.por_equipo = bool(request.form.get("por_equipo"))
        obj.tipo_equipo_id = (
            request.form.get("tipo_equipo_id", type=int) if obj.por_equipo else None
        ) or None
        obj.frecuencia = (request.form.get("frecuencia") or "").strip() or None
        obj.referencia_normativa = (request.form.get("referencia") or "").strip() or None
        obj.orden = request.form.get("orden", type=int) or 0
        obj.incluir_en_paquete = bool(request.form.get("incluir_en_paquete"))
        db.session.flush()

        _guardar_campos(obj)
        db.session.commit()
        flash(f"Formulario guardado con {len(obj.campos)} punto(s).", "ok")
        return redirect(url_for("principal.catalogo"))

    return render_template("formulario_form.html", formulario=obj,
                           categorias=categorias, tipos_equipo=tipos_equipo)


def _guardar_campos(formulario):
    """Reescribe los campos desde el formulario.

    Cada fila lleva su índice en el nombre (`clave_<idx>`) en vez de ir en
    arrays paralelos: con arrays, una casilla sin marcar no se envía y las
    columnas se desalinean en silencio.
    """
    existentes = {c.id: c for c in formulario.campos}
    vistos = set()

    for idx in request.form.getlist("idx"):
        clave = (request.form.get(f"clave_{idx}") or "").strip()
        label = (request.form.get(f"label_{idx}") or "").strip()
        if not clave or not label:
            continue  # fila vacía: se descarta sin ruido

        # El índice de una fila existente ES su id. Se acepta también el
        # campo oculto `id_<idx>`, pero no se depende de él: si faltara,
        # el punto se recrearía y sus respuestas históricas quedarían
        # colgando de una fila que ya no existe.
        campo_id = request.form.get(f"id_{idx}", type=int)
        if not campo_id and idx.isdigit():
            campo_id = int(idx)
        campo = existentes.get(campo_id) if campo_id else None
        if campo is None:
            campo = CampoFormulario(tipo_formulario_id=formulario.id)
            db.session.add(campo)
        else:
            vistos.add(campo.id)

        campo.clave = clave
        campo.label = label
        campo.tipo = request.form.get(f"tipo_{idx}") or CAMPO_ESTADO
        campo.unidad = (request.form.get(f"unidad_{idx}") or "").strip() or None
        campo.opciones_raw = (request.form.get(f"opciones_{idx}") or "").strip() or None
        campo.con_estado = bool(request.form.get(f"con_estado_{idx}"))
        campo.minimo = _numero(request.form.get(f"minimo_{idx}"))
        campo.maximo = _numero(request.form.get(f"maximo_{idx}"))
        campo.gravedad_fuera_rango = (
            request.form.get(f"gravedad_{idx}") or GRAVEDAD_NO_CRITICA
        )
        atributo_equipo = (request.form.get(f"atributo_equipo_{idx}") or "").strip() or None
        campo.atributo_equipo = atributo_equipo if atributo_equipo in ATRIBUTOS_EQUIPO else None
        campo.tolerancia_pct = _numero(request.form.get(f"tolerancia_{idx}")) or 10.0
        campo.frecuencia = (request.form.get(f"frecuencia_{idx}") or "").strip() or None
        campo.ayuda = (request.form.get(f"ayuda_{idx}") or "").strip() or None
        campo.orden = request.form.get(f"orden_{idx}", type=int) or 0

    # Un punto ya respondido no se borra: sus respuestas quedarían colgando
    # y el histórico de esa inspección perdería sentido. Se avisa en vez de
    # romper en silencio.
    conservados = []
    for campo_id, campo in existentes.items():
        if campo_id in vistos:
            continue
        if Respuesta.query.filter_by(campo_id=campo_id).count():
            conservados.append(campo.label)
            continue
        db.session.delete(campo)

    if conservados:
        flash(
            "No se quitaron estos puntos porque ya tienen respuestas cargadas: "
            + ", ".join(f"«{n}»" for n in conservados) + ".",
            "error",
        )


@principal.route("/catalogo/<tipo>/<int:objeto_id>/borrar", methods=["POST"])
@login_required
def catalogo_borrar(tipo, objeto_id):
    """Borra solo lo que no está en uso. Un tipo de equipo con equipos
    cargados o un formulario ya usado en visitas no se puede borrar sin
    romper el histórico: se avisa en vez de fallar."""
    _solo_gestion()
    modelos = {
        "categoria": CategoriaEquipo,
        "tipo-equipo": TipoEquipo,
        "formulario": TipoFormulario,
    }
    modelo = modelos.get(tipo)
    if modelo is None:
        abort(404)
    obj = db.session.get(modelo, objeto_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.empresa_id)

    if tipo == "tipo-equipo":
        usados = Equipo.query.filter_by(tipo_equipo_id=obj.id).count()
        if usados:
            flash(f"No se puede borrar: hay {usados} equipo(s) de este tipo cargados.", "error")
            return redirect(url_for("principal.catalogo"))
    elif tipo == "formulario":
        usados = Formulario.query.filter_by(tipo_formulario_id=obj.id).count()
        if usados:
            flash(f"No se puede borrar: ya se cargó {usados} vez/veces en visitas.", "error")
            return redirect(url_for("principal.catalogo"))
    elif tipo == "categoria":
        usados = TipoEquipo.query.filter_by(categoria_id=obj.id).count()
        if usados:
            flash(f"No se puede borrar: tiene {usados} tipo(s) de equipo.", "error")
            return redirect(url_for("principal.catalogo"))

    db.session.delete(obj)
    db.session.commit()
    flash("Eliminado del catálogo.", "ok")
    return redirect(url_for("principal.catalogo"))


@principal.route("/portal")
@login_required
def portal():
    """Lo que ve el cliente: sus instalaciones y sus deficiencias aprobadas.

    Nada más. No hay órdenes de trabajo, ni el catálogo, ni otros clientes,
    ni lo que un jefe técnico todavía no revisó.
    """
    cliente_obj = current_user.cliente
    if cliente_obj is None:
        return render_template("portal.html", cliente=None, instalaciones=[],
                               abiertas=[], resueltas=[], visitas=[], presupuestos=[])

    instalaciones = (
        Instalacion.query.filter_by(cliente_id=cliente_obj.id)
        .order_by(Instalacion.nombre).all()
    )
    visibles = _deficiencias_visibles(cliente_obj).all()

    ids = [i.id for i in instalaciones]
    visitas_cliente = (
        Visita.query.filter(Visita.instalacion_id.in_(ids))
        .order_by(Visita.fecha.desc()).limit(10).all()
        if ids else []
    )
    # Solo lectura: el cliente ve en qué anda su presupuesto, pero lo
    # aprueba gestión (por ahora sin flujo de aprobación desde el portal).
    presupuestos_cliente = (
        Presupuesto.query.join(Observacion, Presupuesto.observacion_id == Observacion.id)
        .filter(Observacion.instalacion_id.in_(ids))
        .order_by(Presupuesto.fecha_creacion.desc()).all()
        if ids else []
    )

    return render_template(
        "portal.html", cliente=cliente_obj, instalaciones=instalaciones,
        abiertas=[o for o in visibles if not o.resuelto],
        resueltas=[o for o in visibles if o.resuelto],
        visitas=visitas_cliente,
        presupuestos=presupuestos_cliente,
    )


@principal.route("/cuenta")
@login_required
def cuenta():
    return render_template("cuenta.html")


# ---------------------------------------------------------------------------
# Búsqueda global
# ---------------------------------------------------------------------------


@principal.route("/buscar")
@login_required
def buscar():
    q = (request.args.get("q") or "").strip()
    resultados = {"clientes": [], "instalaciones": [], "equipos": [], "visitas": []}

    if len(q) >= 2:
        patron = f"%{q}%"
        empresa = current_user.empresa_id

        resultados["clientes"] = (
            Cliente.query.filter(Cliente.empresa_id == empresa, Cliente.nombre.ilike(patron))
            .order_by(Cliente.nombre).limit(10).all()
        )
        resultados["instalaciones"] = (
            _instalaciones_empresa().filter(Instalacion.nombre.ilike(patron))
            .order_by(Instalacion.nombre).limit(10).all()
        )
        resultados["equipos"] = (
            Equipo.query.join(Instalacion).join(Cliente)
            .filter(
                Cliente.empresa_id == empresa,
                or_(Equipo.nombre.ilike(patron), Equipo.codigo.ilike(patron)),
            )
            .order_by(Equipo.codigo).limit(15).all()
        )
        # Buscar una visita por su número es lo más común en campo.
        crudo = q.lstrip("#")
        if crudo.isdigit():
            encontrada = _visitas_empresa().filter(Visita.id == int(crudo)).first()
            if encontrada:
                resultados["visitas"] = [encontrada]

    total = sum(len(v) for v in resultados.values())
    return render_template("buscar.html", q=q, resultados=resultados, total=total)


@principal.route("/buscar/vivo")
@login_required
def buscar_vivo():
    """Resultados en vivo para el desplegable del buscador — la versión
    corta de `buscar()`, sin re-teclear las mismas queries (revisión UX
    sept. 2026: antes había que apretar Enter y esperar una página nueva
    para ver un solo resultado)."""
    q = (request.args.get("q") or "").strip()
    resultados = {"clientes": [], "instalaciones": [], "equipos": []}

    if len(q) >= 2:
        patron = f"%{q}%"
        empresa = current_user.empresa_id
        resultados["clientes"] = (
            Cliente.query.filter(Cliente.empresa_id == empresa, Cliente.nombre.ilike(patron))
            .order_by(Cliente.nombre).limit(4).all()
        )
        resultados["instalaciones"] = (
            _instalaciones_empresa().filter(Instalacion.nombre.ilike(patron))
            .order_by(Instalacion.nombre).limit(4).all()
        )
        resultados["equipos"] = (
            Equipo.query.join(Instalacion).join(Cliente)
            .filter(
                Cliente.empresa_id == empresa,
                or_(Equipo.nombre.ilike(patron), Equipo.codigo.ilike(patron)),
            )
            .order_by(Equipo.codigo).limit(4).all()
        )

    return render_template("_buscar_vivo.html", q=q, resultados=resultados)
