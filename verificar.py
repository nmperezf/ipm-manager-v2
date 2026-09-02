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
        total = sum(len(b.secciones) for b in beca)
        check("Cuarto de valvulas: 6 secciones", total == 6, f"bloques: {len(beca)}")

        titulos = [b.titulo for b in beca]
        check("Un bloque por estacion + el recinto", len(beca) == 4, " | ".join(titulos))

        conj = {b.titulo: [s.tipo_formulario.nombre for s in b.secciones] for b in beca}
        preaccion = next((v for k, v in conj.items() if "ECA-02" in k), [])
        check("Preaccion: panel antes de la estacion",
              preaccion == ["Panel de detección", "Estación de control de preacción"],
              " -> ".join(preaccion))
        seca = next((v for k, v in conj.items() if "ECA-03" in k), [])
        check("Seca: estacion + compresor",
              seca == ["Estación de control seca", "Compresor de aire"],
              " -> ".join(seca))
        humeda = next((v for k, v in conj.items() if "ECA-01" in k), [])
        check("Humeda va sola - sin aire ni deteccion",
              humeda == ["Estación de control húmeda"], " -> ".join(humeda))

        print("\n8b - Un umbral propio de la categoria")
        t_agua = CampoFormulario.query.filter_by(clave="tiempo_agua").one()
        check("Llegada de agua: maximo 60 s", t_agua.maximo == 60)
        check("75 s dispara deficiencia critica",
              t_agua.fuera_de_rango(75) and t_agua.gravedad_fuera_rango == GRAVEDAD_CRITICA)
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

    print()
    if fallos:
        print(f"FALLARON {len(fallos)}: " + "; ".join(fallos))
        return 1
    print("Todo verde.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
