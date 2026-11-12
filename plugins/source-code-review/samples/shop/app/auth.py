import jwt


def install(app):
    app.config["SECRET"] = "change-me"
