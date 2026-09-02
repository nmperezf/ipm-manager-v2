"""Verificación de los seis cambios de diseño de IPM Manager v2.

No es una suite de tests: es una comprobación de extremo a extremo de que
lo que motivó el proyecto nuevo efectivamente funciona. Corre contra una
base temporal, así que no toca instance/ipm.db.

    python verificar.py
"""

import sys
import tempfile
from datetime import date
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app import crear_app
from app.catalogo_seed import CATEGORIA, CATEGORIA_ECA, sembrar_demo
from app.checklist import armar_bloques, guardar_checklist, nombre_campo
from app.models import (
    CAMPO_SELECCION,
    CLASIF_CRITICA,
    CLASIF_NO_CRITICA,
    ESTADO_CONFORME,
    ESTADO_NO_CONFORME,
    FRECUENCIA_ANUAL,
    FRECUENCIA_MENSUAL,
    FRECUENCIA_SEMESTRAL,
    GRAVEDAD_CRITICA,
    REVISION_APROBADA,
    REVISION_PENDIENTE,
    CampoFormulario,
    CategoriaEquipo,
    Cliente,
    Instalacion,
    ItemVisita,
    Observacion,
    TipoFormulario,
    Usuario,
    Visita,
    db,
)

fallos = []


def check(titulo, condicion, detalle=""):
    marca = "OK  " if condicion else "FALLA"
    print(f"  [{marca}] {titulo}" + (f" - {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(titulo)


def item_para(nombre_cliente, usuario):
    inst = Instalacion.query.join(Cliente).filter(Cliente.nombre == nombre_cliente).one()
    categoria = CategoriaEquipo.query.filter_by(nombre=CATEGORIA).one()
    visita = Visita(instalacion_id=inst.id, fecha=date.today(), tecnico_id=usuario.id)
    db.session.add(visita)
    db.session.flush()
    item = ItemVisita(visita_id=visita.id, categoria_id=categoria.id)
    db.session.add(item)
    db.session.commit()
    return item


def main():
    tmp = Path(tempfile.mkdtemp()) / "verificar.db"
    app = crear_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp}"})

    with app.app_context():
        db.create_all()
        empresa = sembrar_demo()
        jefe = Usuario.query.filter_by(username="jefe").one()
        tecnico = Usuario.query.filter_by(username="tecnico").one()

        print("\n1 - Formularios tipo a nivel empresa")
        tipos = TipoFormulario.query.all()
        check("Todos cuelgan de la empresa", all(t.empresa_id == empresa.id for t in tipos),
              f"{len(tipos)} formularios tipo, 1 sola definicion")
        check("No existe columna cliente_id", not hasattr(TipoFormulario, "cliente_id"))

        print("\n2 - Tipo de campo de opcion unica")
        modo = CampoFormulario.query.filter_by(clave="modo_encendido").first()
        check("Existe 'Modo de encendido'", modo is not None)
        check("Es de opcion unica", modo is not None and modo.tipo == CAMPO_SELECCION,
              ", ".join(modo.opciones) if modo else "-")

        print("\n3 - Rangos esperados por campo")
        combustible = (
            CampoFormulario.query.filter_by(clave="nivel")
            .join(TipoFormulario)
            .filter(TipoFormulario.nombre == "Tanque de combustible")
            .one()
        )
        rodamientos = CampoFormulario.query.filter_by(clave="t_rodamientos").first()
        check("Combustible tiene minimo 66 %", combustible.minimo == 66)
        check("Rodamientos tiene maximo 80 C", rodamientos.maximo == 80)
        check("65 % dispara fuera de rango", combustible.fuera_de_rango(65))
        check("70 % no dispara", not combustible.fuera_de_rango(70))
        check("85 C dispara", rodamientos.fuera_de_rango(85))

        print("\n5 - Orden por conjunto: controlador antes de su bomba")
        for nombre, esperado in (
            ("Torre Ejecutiva", 4),
            ("Planta Industrial Norte", 6),
            ("Hospital Central", 10),
        ):
            item = item_para(nombre, tecnico)
            bloques = armar_bloques(item)
            total = sum(len(b.secciones) for b in bloques)
            check(f"{nombre}: {total} secciones", total == esperado, f"esperado {esperado}")

        item_c = item_para("Hospital Central", tecnico)
        bloques = armar_bloques(item_c)
        conjuntos = [b for b in bloques if b.clave.startswith("conjunto-")]
        check("Hospital tiene 3 conjuntos", len(conjuntos) == 3,
              ", ".join(b.titulo for b in conjuntos))
        if conjuntos:
            nombres = [s.tipo_formulario.nombre for s in conjuntos[0].secciones]
            check("El controlador va primero en el conjunto",
                  bool(nombres) and nombres[0] == "Controlador", " -> ".join(nombres))
        diesel = [b for b in conjuntos if "diesel" in b.titulo.lower()]
        if diesel:
            nd = [s.tipo_formulario.nombre for s in diesel[0].secciones]
            check("Conjunto diesel: controlador -> bomba -> tanque",
                  nd == ["Controlador", "Bomba diesel - prueba sin flujo", "Tanque de combustible"]
                  or nd == ["Controlador", "Bomba diesel — prueba sin flujo", "Tanque de combustible"],
                  " -> ".join(nd))

        print("\n6 - Cruce por clave foranea")
        check("tipo_equipo_id es FK, no texto",
              all(t.tipo_equipo_id is None or t.tipo_equipo is not None for t in tipos))

        print("\n4 - Gravedad elegida por el tecnico + banco de deficiencias")
        item = item_para("Torre Ejecutiva", tecnico)
        bloques = armar_bloques(item)
        seccion_ctrl = next(
            s for b in bloques for s in b.secciones if s.tipo_formulario.nombre == "Controlador"
        )
        tipo, equipo = seccion_ctrl.tipo_formulario, seccion_ctrl.equipo
        campo_selector = next(c for c in tipo.campos if c.clave == "selector_auto")

        form = MultiDict({
            nombre_campo(tipo, equipo, campo_selector, "estado"): ESTADO_NO_CONFORME,
            nombre_campo(tipo, equipo, campo_selector, "gravedad"): GRAVEDAD_CRITICA,
            nombre_campo(tipo, equipo, campo_selector, "comentario"): "Selector en MANUAL.",
        })
        resultado = guardar_checklist(item, form, tecnico)
        check("Guardado sin errores", resultado.ok, str(resultado.errores))
        obs = Observacion.query.filter_by(visita_id=item.visita_id).all()
        check("Se abrio 1 deficiencia", len(obs) == 1)
        check("Entro como CRITICA", bool(obs) and obs[0].clasificacion == CLASIF_CRITICA,
              obs[0].clasificacion if obs else "-")
        check("Queda pendiente - la cargo un tecnico",
              bool(obs) and obs[0].estado_revision == REVISION_PENDIENTE)
        check("El cliente todavia no la ve", bool(obs) and not obs[0].visible_para_cliente)

        print("\n4b - No conforme sin comentario: error, no descarte silencioso")
        item2 = item_para("Torre Ejecutiva", tecnico)
        b2 = armar_bloques(item2)
        s2 = next(s for b in b2 for s in b.secciones if s.tipo_formulario.nombre == "Controlador")
        c2 = next(c for c in s2.tipo_formulario.campos if c.clave == "selector_auto")
        form2 = MultiDict({
            nombre_campo(s2.tipo_formulario, s2.equipo, c2, "estado"): ESTADO_NO_CONFORME,
        })
        r2 = guardar_checklist(item2, form2, tecnico)
        check("Rechaza el guardado", not r2.ok, r2.errores[0] if r2.errores else "")
        check("No escribio nada",
              Observacion.query.filter_by(visita_id=item2.visita_id).count() == 0)

        print("\n3b - Fuera de rango abre la deficiencia solo")
        item3 = item_para("Planta Industrial Norte", tecnico)
        b3 = armar_bloques(item3)
        s3 = next(
            s for b in b3 for s in b.secciones
            if s.tipo_formulario.nombre == "Tanque de combustible"
        )
        c3 = next(c for c in s3.tipo_formulario.campos if c.clave == "nivel")
        form3 = MultiDict({
            nombre_campo(s3.tipo_formulario, s3.equipo, c3, "valor"): "45",
            # El tecnico lo marca Conforme por distraccion:
            nombre_campo(s3.tipo_formulario, s3.equipo, c3, "estado"): ESTADO_CONFORME,
        })
        r3 = guardar_checklist(item3, form3, tecnico)
        check("Guardado sin errores", r3.ok, str(r3.errores))
        o3 = Observacion.query.filter_by(visita_id=item3.visita_id).all()
        check("Abrio deficiencia igual", len(o3) == 1)
        check("Critica, segun el campo", bool(o3) and o3[0].clasificacion == CLASIF_CRITICA,
              o3[0].descripcion if o3 else "-")

        print("\n7 - Auto-aprobacion cuando la visita la hace un jefe tecnico")
        item4 = item_para("Torre Ejecutiva", jefe)
        b4 = armar_bloques(item4)
        s4 = next(s for b in b4 for s in b.secciones if s.tipo_formulario.nombre == "Controlador")
        c4 = next(c for c in s4.tipo_formulario.campos if c.clave == "selector_auto")
        form4 = MultiDict({
            nombre_campo(s4.tipo_formulario, s4.equipo, c4, "estado"): ESTADO_NO_CONFORME,
            nombre_campo(s4.tipo_formulario, s4.equipo, c4, "comentario"): "Selector en MANUAL.",
        })
        r4 = guardar_checklist(item4, form4, jefe)
        check("Guardado sin errores", r4.ok, str(r4.errores))
        o4 = Observacion.query.filter_by(visita_id=item4.visita_id).all()
        check("Nace aprobada", bool(o4) and o4[0].estado_revision == REVISION_APROBADA)
        check("Marcada como automatica", bool(o4) and o4[0].aprobacion_automatica)
        check("El cliente ya la ve", bool(o4) and o4[0].visible_para_cliente)
        check("Gravedad por defecto: no critica",
              bool(o4) and o4[0].clasificacion == CLASIF_NO_CRITICA,
              o4[0].clasificacion if o4 else "-")

        print("\n8 - Estaciones de control y alarma: tres arquitecturas, un paquete")
        cat_eca = CategoriaEquipo.query.filter_by(nombre=CATEGORIA_ECA).one()
        inst_eca = (
            Instalacion.query.join(Cliente)
            .filter(Cliente.nombre == "Centro Comercial Sur").one()
        )
        visita = Visita(instalacion_id=inst_eca.id, fecha=date.today(), tecnico_id=tecnico.id)
        db.session.add(visita)
        db.session.flush()
        item_eca = ItemVisita(visita_id=visita.id, categoria_id=cat_eca.id)
        db.session.add(item_eca)
        db.session.commit()

        beca = armar_bloques(item_eca)
        titulos = [b.titulo for b in beca]
        check("Un bloque por estacion + el recinto", len(beca) == 4, " | ".join(titulos))

        conj = {b.titulo: [s.tipo_formulario.nombre for s in b.secciones] for b in beca}
        preaccion = next((v for k, v in conj.items() if "ECA-02" in k), [])
        check("Preaccion: panel antes de la estacion, compresor despues",
              preaccion == ["Panel de detección", "Estación de control de preacción",
                            "Compresor de aire"],
              " -> ".join(preaccion))
        diluvio = next((v for k, v in conj.items() if "ECA-03" in k), [])
        check("Diluvio: panel antes de la estacion",
              diluvio == ["Panel de detección", "Estación de control de diluvio"],
              " -> ".join(diluvio))
        humeda = next((v for k, v in conj.items() if "ECA-01" in k), [])
        check("Humeda va sola - sin deteccion ni aire",
              humeda == ["Estación de control húmeda"], " -> ".join(humeda))

        print("\n9 - Rutinas acumulativas: mensual < semestral < anual")
        conteos = {}
        for rutina in (FRECUENCIA_MENSUAL, FRECUENCIA_SEMESTRAL, FRECUENCIA_ANUAL):
            item_eca.rutina = rutina
            db.session.commit()
            bl = armar_bloques(item_eca)
            conteos[rutina] = sum(len(s.campos) for b in bl for s in b.secciones)
        check("La mensual es la mas corta", conteos["mensual"] < conteos["semestral"],
              f"mensual {conteos['mensual']} < semestral {conteos['semestral']}")
        check("La anual es la mas larga", conteos["semestral"] < conteos["anual"],
              f"semestral {conteos['semestral']} < anual {conteos['anual']}")

        item_eca.rutina = FRECUENCIA_MENSUAL
        db.session.commit()
        mens = armar_bloques(item_eca)
        s_hum = next(s for b in mens for s in b.secciones
                     if s.tipo_formulario.nombre == "Estación de control húmeda")
        claves = [c.clave for c in s_hum.campos]
        check("Mensual en humeda: solo valvula y presiones",
              claves == ["valvula_principal", "p_suministro", "p_sistema"],
              ", ".join(claves))
        check("La purga del inspector no aparece en la mensual", "purga_abierta" not in claves)

        s_pre = next(s for b in mens for s in b.secciones
                     if s.tipo_formulario.nombre == "Estación de control de preacción")
        check("Preaccion suma la camara de enclavamiento en la mensual",
              "p_enclavamiento" in [c.clave for c in s_pre.campos])
        s_dil = next(s for b in mens for s in b.secciones
                     if s.tipo_formulario.nombre == "Estación de control de diluvio")
        check("Diluvio suma la camara del diafragma en la mensual",
              "p_diafragma" in [c.clave for c in s_dil.campos])

        item_eca.rutina = FRECUENCIA_SEMESTRAL
        db.session.commit()
        sem = armar_bloques(item_eca)
        s_hum6 = next(s for b in sem for s in b.secciones
                      if s.tipo_formulario.nombre == "Estación de control húmeda")
        claves6 = [c.clave for c in s_hum6.campos]
        check("Semestral agrega las senales de supervision y alarma",
              "supervision_valvula" in claves6 and "alarma_flujo" in claves6)
        check("Semestral incluye lo mensual", "p_suministro" in claves6)
        check("Semestral todavia no abre la purga", "purga_abierta" not in claves6)

        item_eca.rutina = FRECUENCIA_ANUAL
        db.session.commit()
        anu = armar_bloques(item_eca)
        s_hum12 = next(s for b in anu for s in b.secciones
                       if s.tipo_formulario.nombre == "Estación de control húmeda")
        claves12 = [c.clave for c in s_hum12.campos]
        for clave in ("purga_abierta", "t_bomba", "t_alarma",
                      "restablecimiento_alarma", "restablecimiento_bomba"):
            check(f"Anual incluye '{clave}'", clave in claves12)
        check("Anual incluye lo semestral y lo mensual",
              "supervision_valvula" in claves12 and "p_suministro" in claves12)

        print("\n8b - Umbrales propios de la categoria")
        t_agua = CampoFormulario.query.filter_by(clave="tiempo_agua").one()
        check("Llegada de agua: maximo 60 s", t_agua.maximo == 60)
        check("75 s dispara deficiencia critica",
              t_agua.fuera_de_rango(75) and t_agua.gravedad_fuera_rango == GRAVEDAD_CRITICA)
        t_alarma = CampoFormulario.query.filter_by(clave="t_alarma").first()
        check("Respuesta de alarma: maximo 90 s", t_alarma.maximo == 90)
        restablecimiento = CampoFormulario.query.filter_by(clave="restablecimiento").one()
        check("Compresor: maximo 30 min", restablecimiento.maximo == 30)

        print("\n8c - Las dos categorias no se mezclan")
        item_bombas = item_para("Hospital Central", tecnico)
        nombres_bombas = {
            s.tipo_formulario.categoria.nombre
            for b in armar_bloques(item_bombas) for s in b.secciones
        }
        nombres_eca = {
            s.tipo_formulario.categoria.nombre for b in beca for s in b.secciones
        }
        check("Sala de bombas solo trae sus formularios", nombres_bombas == {CATEGORIA})
        check("El cuarto de valvulas solo los suyos", nombres_eca == {CATEGORIA_ECA})

        print("\n10 - Calendario predictivo: se calcula, no se guarda")
        from app.planificacion import (
            frecuencias_de_categoria, rutina_del_mes, pendientes_del_mes, coordinar)
        from app.models import Contrato, ServicioContrato

        frecs_eca = frecuencias_de_categoria(cat_eca.id)
        check("ECA declara mensual, semestral y anual",
              frecs_eca == ["mensual", "semestral", "anual"], ", ".join(frecs_eca))

        # Ancla en marzo: la anual cae en marzo y la semestral seis meses despues.
        check("Ancla marzo: marzo da anual",
              rutina_del_mes(3, 2026, 3, frecs_eca) == "anual")
        check("Ancla marzo: septiembre da semestral",
              rutina_del_mes(3, 2026, 9, frecs_eca) == "semestral")
        check("Ancla marzo: abril da mensual",
              rutina_del_mes(3, 2026, 4, frecs_eca) == "mensual")
        check("La acumulativa gana: nunca devuelve la menor si cae una mayor",
              rutina_del_mes(1, 2026, 1, frecs_eca) == "anual")

        # El seed ya deja un contrato para el cuarto de válvulas con ancla en
        # marzo. Se usa ese en vez de inventar otro: así se verifica también
        # que el seed quedó bien armado.
        contrato = Contrato.query.filter_by(instalacion_id=inst_eca.id).one()
        servicio = contrato.servicios[0]
        check("El seed dejó el contrato con ancla en marzo", servicio.mes_ancla == 3)

        def suyos(anio, mes):
            """Solo los pendientes de esta instalación: las otras del seed
            tienen sus propios contratos y ensuciarían el conteo."""
            return [p for p in pendientes_del_mes(empresa.id, anio, mes)
                    if p["instalacion"].id == inst_eca.id]

        pend = suyos(2027, 3)
        check("El contrato genera pendiente en marzo", len(pend) == 1)
        check("Con la rutina anual", bool(pend) and pend[0]["rutina"] == "anual",
              pend[0]["rutina"] if pend else "-")

        pend9 = suyos(2027, 9)
        check("Y semestral en septiembre",
              bool(pend9) and pend9[0]["rutina"] == "semestral",
              pend9[0]["rutina"] if pend9 else "-")

        orden = coordinar(servicio, date(2027, 3, 10), "anual", tecnico, empresa.id)
        # La OT se numera por su año de apertura (hoy), no por la fecha
        # acordada: es el número con el que se abre el trabajo.
        check("Coordinar crea la OT numerada",
              orden.numero.startswith(f"OT-{date.today().year}-"), orden.numero)
        check("Con su visita en la fecha acordada", orden.visita.fecha == date(2027, 3, 10))
        check("Y su item en la rutina correcta", orden.visita.items[0].rutina == "anual")
        check("Marzo deja de estar pendiente", len(suyos(2027, 3)) == 0)

        contrato.activo = False
        db.session.commit()
        check("Contrato de baja: deja de generar pendientes", len(suyos(2027, 9)) == 0)

        print("\n11 - Un punto conforme no deja hallazgo fantasma")
        # Regresion: Jinja escribia el None de Python como la palabra "None"
        # dentro del textarea de comentario. El navegador la reenviaba y el
        # servidor abria una observacion "Campo: None" en cada guardado.
        item_r = item_para("Torre Ejecutiva", tecnico)
        b_r = armar_bloques(item_r)
        s_r = next(s for b in b_r for s in b.secciones)
        c_r = s_r.campos[0]
        base = {
            nombre_campo(s_r.tipo_formulario, s_r.equipo, c_r, "estado"): ESTADO_CONFORME,
        }
        res = guardar_checklist(item_r, MultiDict(base), tecnico)
        check("Guarda el punto conforme", res.ok, str(res.errores))
        check("Sin abrir ninguna observacion",
              Observacion.query.filter_by(visita_id=item_r.visita_id).count() == 0)

        # Segundo guardado reenviando el comentario vacio, que es lo que
        # manda el navegador al repintar la pantalla.
        con_vacio = dict(base)
        con_vacio[nombre_campo(s_r.tipo_formulario, s_r.equipo, c_r, "comentario")] = ""
        guardar_checklist(item_r, MultiDict(con_vacio), tecnico)
        check("Reenviar el comentario vacio tampoco abre nada",
              Observacion.query.filter_by(visita_id=item_r.visita_id).count() == 0)

        # Y el comentario real sigue funcionando, pero como nota.
        con_nota = dict(base)
        con_nota[nombre_campo(s_r.tipo_formulario, s_r.equipo, c_r, "comentario")] = "Se repinto."
        guardar_checklist(item_r, MultiDict(con_nota), tecnico)
        notas = Observacion.query.filter_by(visita_id=item_r.visita_id).all()
        check("Un comentario real si deja nota", len(notas) == 1)
        check("Clasificada como Comentario, no como deficiencia",
              bool(notas) and notas[0].clasificacion == "Comentario",
              notas[0].clasificacion if notas else "-")

        print("\n12 - Banco de fotos ligado al equipo")
        import io as _io
        from PIL import Image as _Image
        from werkzeug.datastructures import FileStorage
        from app.fotos import FotoInvalida, guardar_archivo
        from app.models import Equipo, Foto

        def _imagen(ancho, alto):
            buf = _io.BytesIO()
            _Image.new("RGB", (ancho, alto), (70, 110, 150)).save(buf, "JPEG")
            buf.seek(0)
            return FileStorage(stream=buf, filename="IMG_0001.jpg")

        # Una foto de tablet ronda los 4000 px de lado: se reescala.
        nombre, an, al, peso = guardar_archivo(app, _imagen(4032, 3024), empresa.id)
        check("Reescala la foto de tablet", (an, al) == (1600, 1200), f"{an}x{al}")
        check("Y pesa poco", peso < 400 * 1024, f"{peso // 1024} KB")

        item_f = item_para("Torre Ejecutiva", tecnico)
        equipo_f = Equipo.query.filter_by(
            instalacion_id=item_f.visita.instalacion_id).first()
        foto = Foto(
            instalacion_id=item_f.visita.instalacion_id,
            equipo_id=equipo_f.id, item_visita_id=item_f.id,
            clave_campo="temperatura", archivo=nombre,
            ancho=an, alto=al, bytes=peso, tomada_por_id=tecnico.id,
        )
        db.session.add(foto)
        db.session.commit()

        check("Queda ligada al equipo", foto.equipo.id == equipo_f.id, equipo_f.etiqueta)
        check("Y por eso se navega por tipo de equipo",
              foto.tipo_equipo is not None and
              foto.tipo_equipo.id == equipo_f.tipo_equipo_id,
              foto.tipo_equipo.nombre if foto.tipo_equipo else "-")
        check("Recuerda en qué punto se sacó", foto.clave_campo == "temperatura")

        # Se cuelga del ítem y la clave, no de la Respuesta: cuando el
        # técnico saca la foto el checklist todavía no se guardó.
        check("No necesita que el checklist esté guardado",
              foto.item_visita_id == item_f.id and
              not any(f.respuestas for f in item_f.formularios))

        for archivo, motivo in (("virus.exe", "extensión"), ("falsa.jpg", "contenido")):
            malo = FileStorage(stream=_io.BytesIO(b"no soy una imagen"), filename=archivo)
            try:
                guardar_archivo(app, malo, empresa.id)
                check(f"Rechaza {archivo} por {motivo}", False)
            except FotoInvalida:
                check(f"Rechaza {archivo} por {motivo}", True)

    print()
    if fallos:
        print(f"FALLARON {len(fallos)}: " + "; ".join(fallos))
        return 1
    print("Todo verde.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
