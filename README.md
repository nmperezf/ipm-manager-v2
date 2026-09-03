# IPM Manager v2

Gestión de mantenimiento de sistemas contra incendio. Reescritura del motor
de formularios y del banco de deficiencias de IPM Service.

## Por qué un proyecto nuevo

El sistema anterior funcionaba, pero seis decisiones de modelo hacían que el
paquete de inspección no fuera realmente reutilizable y que el técnico no
pudiera reportar bien lo que encontraba. Los seis están resueltos de raíz acá:

| # | Antes | Ahora |
|---|---|---|
| 1 | `TipoFormulario` colgaba del cliente | Cuelga de la **empresa**: NFPA 25 no cambia por cliente |
| 2 | Sin tipo de campo de opción única | Existe `seleccion` (Modo de encendido: Test / Automático / Manual) |
| 3 | Sin rangos esperados | `minimo`/`maximo` por campo; fuera de rango abre la deficiencia solo |
| 4 | Gravedad fija en el código | La **elige el técnico**: crítica o no crítica |
| 5 | Secciones agrupadas por formulario | Agrupadas por **conjunto**: controlador → bomba → tanque |
| 6 | Cruce equipo↔formulario por texto | Clave foránea: renombrar un tipo ya no rompe nada en silencio |

## Cómo se conecta un formulario a una visita

El formulario nunca conoce los equipos concretos. Declara a qué **tipo** de
equipo aplica, y el cruce se hace en el momento de la visita contra el
inventario real de esa instalación. Si no hay ningún equipo de ese tipo, la
sección no aparece.

- **NFPA 20** describe lo que varía — qué componentes tiene la sala → `Equipo`
- **NFPA 25** describe lo que es genérico — qué se prueba y cada cuánto → `TipoFormulario`

El mismo paquete de sala de bombas produce checklists distintos sin ninguna
condicional en el código:

| Sala | Inventario | Secciones |
|---|---|---|
| Torre Ejecutiva | 1 eléctrica + controlador + jockey | 4 |
| Planta Industrial Norte | 1 diesel + controlador + tanque + jockey + reserva | 6 |
| Hospital Central | 2 eléctricas + 1 diesel, c/u con controlador, + tanque + jockey + reserva | 10 |

## Banco de deficiencias

1. El técnico marca **No conforme** → comentario obligatorio + gravedad.
2. La observación entra al banco del cliente como `Pendiente`. **El cliente no la ve.**
3. Un **jefe técnico** la aprueba → recién ahí aparece en el portal y en los PDF.
4. **Atajo:** si la visita la hizo un jefe técnico, nace aprobada y queda marcada
   como aprobación automática.

Un punto **conforme** con comentario deja una nota de clase `Comentario`, no una
deficiencia.

## Puesta en marcha

```bash
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt

venv/Scripts/python -m flask --app run.py seed   # catálogo + 3 salas de ejemplo
venv/Scripts/python run.py                       # http://localhost:5001
```

Usuarios de demostración:

- `jefe / jefe` — jefe técnico, sus deficiencias se aprueban solas
- `tecnico / tecnico` — técnico, quedan pendientes de aprobación

## Verificación

```bash
venv/Scripts/python verificar.py
```

Comprueba de extremo a extremo los seis cambios más la auto-aprobación,
contra una base temporal. No toca `instance/ipm.db`.

## Estructura

```
app/
  models.py         dominio completo — las 14 tablas
  checklist.py      armado de bloques y guardado (los cambios 3, 4 y 5)
  catalogo_seed.py  paquete de sala de bombas + datos de demostración
  routes.py         login, instalaciones, checklist, banco de deficiencias
  templates/        base + login + inicio + checklist + deficiencias
run.py              entrypoint y comandos CLI
verificar.py        verificación de los seis cambios
```

## Pendiente

- Las **frecuencias** del catálogo siguen NFPA 25 pero hay que confirmarlas
  contra la edición a la que se certifica. La prueba sin flujo de bombas
  eléctricas pasó de semanal a mensual en ediciones recientes; las diesel
  siguen siendo semanales.
- Los rangos **por equipo** (presiones, caudal, RPM) ya se usan al validar la
  carga (`CampoFormulario.atributo_equipo` + `tolerancia_pct`, ver
  `checklist.guardar_checklist`). La tolerancia por defecto es **±10 %**
  alrededor del valor de placa — un placeholder razonable, no un criterio de
  aceptación de NFPA 20 confirmado. Falta contrastarla contra la norma
  (p.ej. el criterio de curva de fábrica de la prueba anual de caudal).
- Falta el resto del CMMS: PDF. (Contratos, coordinación mensual, órdenes de
  trabajo y portal del cliente ya están implementados.)
