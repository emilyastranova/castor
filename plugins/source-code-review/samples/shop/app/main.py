from flask import Flask

from .api import orders
from .db.session import connect
import app.auth as auth


def create_app():
    app = Flask(__name__)
    app.register_blueprint(orders.bp)
    auth.install(app)
    connect()
    return app


if __name__ == "__main__":
    create_app().run()
