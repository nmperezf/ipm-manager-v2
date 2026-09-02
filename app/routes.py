"""Rutas de IPM Manager v2.

Estructura del CMMS: panorama, clientes e instalaciones, visitas con su
checklist, banco de deficiencias y catálogo de la empresa.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from app.checklist import armar_bloques, guardar_checklist, nombre_campo
from app.fotos import FotoInvalida, borrar_archivo, guardar_archivo, ruta_relativa
from app.planificacion import (
    MESES,
    calendario_anual,
    categorias_disponibles,
    coordinar,
    pendientes_del_mes,
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
    FRECUENCIA_MENSUAL,
    FRECUENCIAS,
    GRAVEDAD_CRITICA,
    GRAVEDAD_NO_CRITICA,
    REVISION_APROBADA,
    REVISION_PENDIENTE,
    ESTADOS_OT,
    OT_CERRADA,
    OT_EN_CURSO,
    OT_EN_REVISION,
    OT_PENDIENTE,
    OT_PREVENTIVO,
    CategoriaEquipo,
    Cliente,
    Contrato,
    Equipo,
    Foto,
    Instalacion,
    ItemVisita,
    Observacion,
    OrdenTrabajo,
    ServicioContrato,
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
        "ESTADOS_OT": ESTADOS_OT,
        "OT_PENDIENTE": OT_PENDIENTE,
        "OT_EN_CURSO": OT_EN_CURSO,
        "OT_EN_REVISION": OT_EN_REVISION,
        "OT_CERRADA": OT_CERRADA,
        "nombre_campo": nombre_campo,
        "pendientes_aprobacion": 0,
    }
    if current_user.is_authenticated:
        contexto["pendientes_aprobacion"] = _observaciones_empresa().filter(
            Observacion.estado_revision == REVISION_PENDIENTE
        ).count()
    return contexto


def _verificar_empresa(empresa_id):
    if empresa_id != current_user.empresa_id:
        abort(403)


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
        return redirect(url_for("principal.inicio"))
    if request.method == "POST":
        usuario = Usuario.query.filter_by(
            username=request.form.get("username", "").strip()
        ).first()
        if usuario and usuario.activo and usuario.check_password(request.form.get("password", "")):
            login_user(usuario)
            return redirect(url_for("principal.inicio"))
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


@principal.route("/coordinacion")
@login_required
def coordinacion():
    """Lo que los contratos mandan hacer este mes y todavía no se hizo.

    No hay tabla de solicitudes: se calcula. Cambiar un contrato se refleja
    en el acto y no queda nada viejo dando vueltas.
    """
    _solo_gestion()
    hoy = date.today()
    anio = request.args.get("anio", type=int) or hoy.year
    mes = request.args.get("mes", type=int) or hoy.month
    mes = min(12, max(1, mes))

    pendientes = pendientes_del_mes(current_user.empresa_id, anio, mes)
    tecnicos = (
        Usuario.query.filter_by(empresa_id=current_user.empresa_id, activo=True)
        .filter(Usuario.rol.in_(("Técnico", "Jefe técnico")))
        .order_by(Usuario.nombre_completo).all()
    )
    # Lo ya coordinado del mes, para no dejar la pantalla en blanco cuando
    # se terminó de coordinar todo.
    primero = date(anio, mes, 1)
    ultimo = date(anio, mes, monthrange(anio, mes)[1])
    coordinadas = (
        _ordenes_empresa()
        .filter(Visita.fecha >= primero, Visita.fecha <= ultimo)
        .order_by(Visita.fecha).all()
    )

    return render_template(
        "coordinacion.html", pendientes=pendientes, coordinadas=coordinadas,
        tecnicos=tecnicos, anio=anio, mes=mes, MESES=MESES,
        hoy=hoy, sugerida=max(hoy, primero) if hoy <= ultimo else primero,
    )


@principal.route("/coordinar/<int:servicio_id>", methods=["POST"])
@login_required
def coordinar_servicio(servicio_id):
    _solo_gestion()
    servicio = db.session.get(ServicioContrato, servicio_id)
    if servicio is None:
        abort(404)
    _verificar_empresa(servicio.contrato.instalacion.cliente.empresa_id)

    fecha = _fecha(request.form.get("fecha"))
    if fecha is None:
        flash("Hace falta una fecha para coordinar.", "error")
        return redirect(request.referrer or url_for("principal.coordinacion"))

    rutina = request.form.get("rutina") or FRECUENCIA_MENSUAL
    tecnico = db.session.get(Usuario, request.form.get("tecnico_id", type=int) or 0)
    if tecnico and tecnico.empresa_id != current_user.empresa_id:
        tecnico = None

    orden = coordinar(servicio, fecha, rutina, tecnico, current_user.empresa_id)
    flash(f"{orden.numero} creada para el {fecha.strftime('%d/%m/%Y')}.", "ok")
    return redirect(url_for("principal.coordinacion",
                            anio=fecha.year, mes=fecha.month))


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


@principal.route("/orden/<int:orden_id>")
@login_required
def orden(orden_id):
    obj = db.session.get(OrdenTrabajo, orden_id)
    if obj is None:
        abort(404)
    _verificar_empresa(obj.visita.instalacion.cliente.empresa_id)

    # El primer ítem sin cargar es a donde el técnico tiene que ir.
    siguiente = next((i for i in obj.visita.items if not i.formularios), None)
    return render_template("orden.html", orden=obj, siguiente=siguiente)


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
    """Alta unificada: cliente y, si se completa, su primera instalación en
    un solo guardado."""
    _solo_gestion()

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre del cliente es obligatorio.", "error")
            return render_template("cliente_form.html", cliente=None, datos=request.form)

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

        db.session.commit()

        if creada:
            flash(f"Cliente e instalación creados. Cargá los equipos de «{creada.nombre}».", "ok")
            return redirect(url_for("principal.instalacion", instalacion_id=creada.id))
        flash("Cliente creado.", "ok")
        return redirect(url_for("principal.cliente", cliente_id=nuevo.id))

    return render_template("cliente_form.html", cliente=None, datos={})


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
    return render_template("checklist.html", item=item, bloques=bloques)


# ---------------------------------------------------------------------------
# Banco de fotos
# ---------------------------------------------------------------------------


def fotos_de_punto(item, equipo, clave):
    """Las fotos ya subidas para ese punto, para repintarlas al recargar."""
    return (
        Foto.query.filter_by(
            item_visita_id=item.id,
            equipo_id=equipo.id if equipo else None,
            clave_campo=clave,
        )
        .order_by(Foto.id)
        .all()
    )


principal.add_app_template_global(fotos_de_punto, "fotos_de_punto")
principal.add_app_template_global(ruta_relativa, "ruta_foto")


@principal.route("/foto/subir", methods=["POST"])
@login_required
def subir_foto():
    """Sube una foto sola, apenas se saca.

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
        clave_campo=(request.form.get("clave") or "").strip() or None,
        archivo=nombre,
        nombre_original=(request.files["foto"].filename or "")[:255],
        ancho=ancho, alto=alto, bytes=peso,
        tomada_por_id=current_user.id,
    )
    db.session.add(foto)
    db.session.commit()

    return jsonify(
        ok=True, id=foto.id,
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
