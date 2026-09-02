"""Motor de checklist: qué se le muestra al técnico y qué pasa al guardar.

Dos responsabilidades:

- `armar_bloques` cruza los formularios tipo de la empresa con los equipos
  reales de la instalación. Los formularios no conocen equipos concretos:
  declaran a qué tipo aplican y el cruce se hace acá. Si la instalación no
  tiene ningún equipo de ese tipo, la sección no aparece — eso es lo que
  hace que un mismo paquete sirva para cualquier sala.

- `guardar_checklist` persiste las respuestas y abre las observaciones que
  correspondan, con la gravedad que eligió el técnico o la que dispara el
  rango del campo.
"""

from collections import namedtuple

from app.models import (
    CAMPO_MULTI,
    CAMPO_NUMERO,
    CLASIF_COMENTARIO,
    ESTADO_NA,
    ESTADO_NO_CONFORME,
    GRAVEDAD_A_CLASIFICACION,
    GRAVEDAD_NO_CRITICA,
    Formulario,
    Observacion,
    Respuesta,
    TipoFormulario,
    db,
)

# Una sección es un formulario tipo aplicado a un equipo concreto (o al
# recinto entero, con equipo=None).
Seccion = namedtuple("Seccion", "tipo_formulario equipo formulario respuestas")

# Un bloque agrupa las secciones que el técnico recorre juntas: el recinto,
# un equipo suelto, o un conjunto (bomba principal con su controlador y su
# tanque colgando).
Bloque = namedtuple("Bloque", "clave titulo subtitulo equipo_cabeza secciones orden")


class ResultadoGuardado(namedtuple("ResultadoGuardado", "formularios respuestas observaciones errores")):
    @property
    def ok(self):
        return not self.errores


# ---------------------------------------------------------------------------
# Armado de la pantalla
# ---------------------------------------------------------------------------


def _tipos_de_la_categoria(item, incluir_fuera_de_paquete=False):
    """Formularios tipo de la empresa que pertenecen a la categoría del
    ítem, ordenados. El filtro por `incluir_en_paquete` es lo que deja la
    prueba anual de caudal fuera de la inspección de rutina."""
    instalacion = item.visita.instalacion
    empresa_id = instalacion.cliente.empresa_id

    query = TipoFormulario.query.filter(
        TipoFormulario.empresa_id == empresa_id,
        TipoFormulario.categoria_id == item.categoria_id,
    )
    if not incluir_fuera_de_paquete:
        query = query.filter(TipoFormulario.incluir_en_paquete.is_(True))
    return query.order_by(TipoFormulario.orden, TipoFormulario.nombre).all()


def _formularios_cargados(item):
    """Lo ya guardado en este ítem, indexado por (tipo_formulario, equipo)
    para poder repintar la pantalla con los valores puestos."""
    cargados = {}
    for formulario in Formulario.query.filter_by(item_visita_id=item.id).all():
        cargados[(formulario.tipo_formulario_id, formulario.equipo_id)] = formulario
    return cargados


def _respuestas_por_campo(formulario):
    if not formulario:
        return {}
    return {r.campo_id: r for r in formulario.respuestas}


def _seccion(tipo, equipo, cargados):
    formulario = cargados.get((tipo.id, equipo.id if equipo else None))
    return Seccion(
        tipo_formulario=tipo,
        equipo=equipo,
        formulario=formulario,
        respuestas=_respuestas_por_campo(formulario),
    )


def _descendientes(equipo):
    ids = set()
    for hijo in equipo.hijos:
        ids.add(hijo.id)
        ids.update(_descendientes(hijo))
    return ids


def armar_bloques(item, incluir_fuera_de_paquete=False):
    """Devuelve los bloques a recorrer, en el orden en que se cargan.

    El orden no es plano por tipo de formulario: los equipos que encabezan
    un conjunto (las bombas principales) arrastran a sus hijos, de modo
    que el técnico recorre *controlador → bomba → tanque* por cada bomba,
    en vez de todos los controladores juntos y después todas las bombas.
    """
    instalacion = item.visita.instalacion
    tipos = _tipos_de_la_categoria(item, incluir_fuera_de_paquete)
    cargados = _formularios_cargados(item)

    # Formularios tipo indexados por el tipo de equipo al que aplican.
    tipos_por_equipo = {}
    tipos_de_recinto = []
    for tipo in tipos:
        if tipo.por_equipo and tipo.tipo_equipo_id:
            tipos_por_equipo.setdefault(tipo.tipo_equipo_id, []).append(tipo)
        elif not tipo.por_equipo:
            tipos_de_recinto.append(tipo)

    equipos = [e for e in instalacion.equipos if e.activo]
    # Solo interesan los equipos para los que existe algún formulario en
    # esta categoría. El resto no tiene nada que cargar acá.
    equipos = [e for e in equipos if e.tipo_equipo_id in tipos_por_equipo]

    bloques = []

    # 1 · El recinto: una sola vez, sin equipo.
    if tipos_de_recinto:
        secciones = [_seccion(tipo, None, cargados) for tipo in tipos_de_recinto]
        bloques.append(
            Bloque(
                clave="recinto",
                titulo=tipos_de_recinto[0].nombre if len(tipos_de_recinto) == 1 else "Recinto",
                subtitulo="Se carga una vez para toda la sala",
                equipo_cabeza=None,
                secciones=secciones,
                orden=min(t.orden for t in tipos_de_recinto),
            )
        )

    # 2 · Conjuntos: una bomba principal con lo que le cuelga.
    cabezas = [e for e in equipos if e.tipo_equipo.encabeza_conjunto]
    ids_en_conjunto = set()
    for cabeza in cabezas:
        ids_en_conjunto.add(cabeza.id)
        ids_en_conjunto.update(_descendientes(cabeza))

    for cabeza in cabezas:
        miembros = [cabeza] + [e for e in equipos if e.padre_id == cabeza.id]
        secciones = []
        for equipo in miembros:
            for tipo in tipos_por_equipo.get(equipo.tipo_equipo_id, []):
                secciones.append(_seccion(tipo, equipo, cargados))
        if not secciones:
            continue
        # Dentro del conjunto manda el orden del formulario tipo, no el del
        # equipo: así el controlador (orden menor) queda antes que su bomba
        # aunque sea un equipo hijo.
        secciones.sort(key=lambda s: (s.tipo_formulario.orden, s.tipo_formulario.nombre))
        bloques.append(
            Bloque(
                clave=f"conjunto-{cabeza.id}",
                titulo=cabeza.etiqueta,
                subtitulo=cabeza.ubicacion or cabeza.tipo_equipo.nombre,
                equipo_cabeza=cabeza,
                secciones=secciones,
                orden=min(s.tipo_formulario.orden for s in secciones),
            )
        )

    # 3 · Equipos que no pertenecen a ningún conjunto (reserva, jockey).
    for equipo in equipos:
        if equipo.id in ids_en_conjunto:
            continue
        secciones = [
            _seccion(tipo, equipo, cargados)
            for tipo in tipos_por_equipo.get(equipo.tipo_equipo_id, [])
        ]
        if not secciones:
            continue
        bloques.append(
            Bloque(
                clave=f"equipo-{equipo.id}",
                titulo=equipo.etiqueta,
                subtitulo=equipo.ubicacion or equipo.tipo_equipo.nombre,
                equipo_cabeza=equipo,
                secciones=secciones,
                orden=min(s.tipo_formulario.orden for s in secciones),
            )
        )

    bloques.sort(key=lambda b: (b.orden, b.titulo))
    return bloques


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------


def nombre_campo(tipo_formulario, equipo, campo, sufijo):
    """Nombre del input en el HTML. El 0 como id de equipo es el centinela
    para las secciones de recinto, que no cuelgan de ningún equipo."""
    equipo_id = equipo.id if equipo else 0
    return f"{sufijo}__{tipo_formulario.id}__{equipo_id}__{campo.clave}"


def _leer_valor(form, tipo_formulario, equipo, campo):
    clave = nombre_campo(tipo_formulario, equipo, campo, "valor")
    if campo.tipo == CAMPO_MULTI:
        seleccionadas = form.getlist(clave)
        return " | ".join(seleccionadas) if seleccionadas else None
    valor = (form.get(clave) or "").strip()
    return valor or None


def _donde(tipo_formulario, equipo):
    return f"{equipo.etiqueta} · {tipo_formulario.nombre}" if equipo else tipo_formulario.nombre


def guardar_checklist(item, form, usuario):
    """Persiste lo cargado y abre las observaciones que correspondan.

    Reglas:
    - Un punto marcado No conforme exige comentario. Sin comentario es un
      error de validación, no un descarte silencioso: perder la deficiencia
      porque el técnico no escribió nada es peor que rechazar el guardado.
    - Un valor numérico fuera del rango universal del campo abre la no
      conformidad solo, con la gravedad declarada en el campo.
    - Si la visita la hizo alguien que puede aprobar (jefe técnico o
      administrador), las observaciones nacen aprobadas.
    """
    instalacion = item.visita.instalacion
    aprueba_solo = bool(usuario and usuario.puede_aprobar)

    errores = []
    formularios_tocados = 0
    respuestas_guardadas = 0
    observaciones_creadas = 0

    bloques = armar_bloques(item, incluir_fuera_de_paquete=True)
    secciones = [s for bloque in bloques for s in bloque.secciones]

    # Primera pasada: validar. No se escribe nada hasta que todo el
    # checklist sea consistente.
    pendientes = []
    for seccion in secciones:
        tipo, equipo = seccion.tipo_formulario, seccion.equipo
        for campo in tipo.campos:
            valor = _leer_valor(form, tipo, equipo, campo)
            estado = (form.get(nombre_campo(tipo, equipo, campo, "estado")) or "").strip() or None
            gravedad = (form.get(nombre_campo(tipo, equipo, campo, "gravedad")) or "").strip() or None
            comentario = (form.get(nombre_campo(tipo, equipo, campo, "comentario")) or "").strip() or None

            numero = None
            if campo.tipo == CAMPO_NUMERO and valor is not None:
                try:
                    numero = float(str(valor).replace(",", "."))
                except ValueError:
                    errores.append(
                        f"{_donde(tipo, equipo)} — «{campo.label}»: «{valor}» no es un número."
                    )
                    continue

            # El rango universal puede forzar la no conformidad aunque el
            # técnico haya marcado Conforme por distracción.
            automatico = False
            if numero is not None and campo.fuera_de_rango(numero):
                automatico = True
                estado = ESTADO_NO_CONFORME
                if not gravedad:
                    gravedad = campo.gravedad_fuera_rango or GRAVEDAD_NO_CRITICA
                if not comentario:
                    unidad = f" {campo.unidad}" if campo.unidad else ""
                    comentario = (
                        f"Valor {numero:g}{unidad} fuera del rango esperado "
                        f"({campo.texto_rango()})."
                    )

            if estado == ESTADO_NO_CONFORME:
                if not comentario:
                    errores.append(
                        f"{_donde(tipo, equipo)} — «{campo.label}»: "
                        "una no conformidad necesita comentario."
                    )
                    continue
                if not gravedad:
                    gravedad = GRAVEDAD_NO_CRITICA

            if valor is None and not estado and not comentario:
                continue

            pendientes.append(
                (seccion, campo, numero, valor, estado, gravedad, comentario, automatico)
            )

    if errores:
        return ResultadoGuardado(0, 0, 0, errores)

    # Segunda pasada: escribir.
    formularios = {}
    for seccion, campo, numero, valor, estado, gravedad, comentario, automatico in pendientes:
        tipo, equipo = seccion.tipo_formulario, seccion.equipo
        clave = (tipo.id, equipo.id if equipo else None)

        formulario = formularios.get(clave) or seccion.formulario
        if formulario is None:
            formulario = Formulario(
                item_visita_id=item.id,
                tipo_formulario_id=tipo.id,
                equipo_id=equipo.id if equipo else None,
            )
            db.session.add(formulario)
            db.session.flush()
            formularios_tocados += 1
        formularios[clave] = formulario

        respuesta = seccion.respuestas.get(campo.id)
        if respuesta is None:
            respuesta = Respuesta(formulario_id=formulario.id, campo_id=campo.id)
            db.session.add(respuesta)

        respuesta.valor_numero = numero
        respuesta.valor_texto = None if numero is not None else valor
        respuesta.estado = estado
        respuesta.gravedad = gravedad if estado == ESTADO_NO_CONFORME else None
        respuesta.comentario = comentario
        respuesta.disparo_automatico = automatico
        db.session.flush()
        respuestas_guardadas += 1

        if _abrir_observacion(
            respuesta, campo, estado, gravedad, comentario, equipo,
            instalacion, item, usuario, aprueba_solo,
        ):
            observaciones_creadas += 1

    db.session.commit()
    return ResultadoGuardado(formularios_tocados, respuestas_guardadas, observaciones_creadas, [])


def _abrir_observacion(respuesta, campo, estado, gravedad, comentario, equipo,
                       instalacion, item, usuario, aprueba_solo):
    """Abre (o actualiza) la observación ligada a este punto.

    Un punto conforme con comentario deja una nota de clase Comentario, no
    una deficiencia. Si el punto deja de ser no conforme, la observación
    que había se borra: no tiene sentido arrastrar una deficiencia de un
    punto que se corrigió en la misma carga.
    """
    existente = respuesta.observacion

    if not comentario or estado == ESTADO_NA:
        if existente:
            db.session.delete(existente)
        return False

    if estado == ESTADO_NO_CONFORME:
        clasificacion = GRAVEDAD_A_CLASIFICACION.get(
            gravedad, GRAVEDAD_A_CLASIFICACION[GRAVEDAD_NO_CRITICA]
        )
    else:
        clasificacion = CLASIF_COMENTARIO

    descripcion = f"{campo.label}: {comentario}"

    if existente:
        existente.clasificacion = clasificacion
        existente.descripcion = descripcion
        return False

    observacion = Observacion(
        instalacion_id=instalacion.id,
        equipo_id=equipo.id if equipo else None,
        respuesta_id=respuesta.id,
        visita_id=item.visita_id,
        clasificacion=clasificacion,
        descripcion=descripcion,
        creado_por_id=usuario.id if usuario else None,
    )
    if aprueba_solo:
        observacion.aprobar(usuario, automatica=True)
    db.session.add(observacion)
    return True
