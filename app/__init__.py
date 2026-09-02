"""Fábrica de la aplicación IPM Manager v2."""

import os

from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate

from app.models import Usuario, db

login_manager = LoginManager()
login_manager.login_view = "principal.login"
login_manager.login_message = "Iniciá sesión para continuar."

migrate = Migrate()


@login_manager.user_loader
def cargar_usuario(user_id):
    return db.session.get(Usuario, int(user_id))


def crear_app(config=None):
    app = Flask(__name__, instance_relative_config=True)

    os.makedirs(app.instance_path, exist_ok=True)
    ruta_db = os.path.join(app.instance_path, "ipm.db")

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-ipm-manager-v2"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", f"sqlite:///{ruta_db}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    if config:
        app.config.update(config)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    from app.routes import principal

    app.register_blueprint(principal)

    return app
