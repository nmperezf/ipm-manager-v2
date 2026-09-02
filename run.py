"""Punto de entrada de IPM Manager v2.

    python run.py                  # levanta el servidor de desarrollo
    flask --app run.py init-db     # crea las tablas
    flask --app run.py seed        # catálogo de sala de bombas + demo
"""

import click

from app import crear_app
from app.catalogo_seed import sembrar_demo
from app.models import db

app = crear_app()


@app.cli.command("init-db")
def init_db():
    """Crea las tablas desde cero."""
    db.create_all()
    click.echo("Tablas creadas.")


@app.cli.command("seed")
def seed():
    """Carga el catálogo de sala de bombas y tres instalaciones de ejemplo."""
    db.create_all()
    empresa = sembrar_demo()
    click.echo(f"Catálogo y demo cargados para «{empresa.nombre}».")
    click.echo("Usuarios: jefe/jefe (aprueba solo) · tecnico/tecnico (queda pendiente)")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
