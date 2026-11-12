"""Local web controller and the original JSON command/status endpoints."""
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException


def create_app(robot):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 4096

    @app.before_request
    def same_origin():
        # Block browser requests originating from an unrelated site. CLI clients
        # may omit Origin. No CORS is enabled; use only on a trusted LAN.
        if request.method == "POST":
            from urllib.parse import urlsplit

            origin = request.headers.get("Origin")
            if origin is not None and urlsplit(origin).netloc != request.host:
                return jsonify(error="Cross-origin control is not allowed"), 403

    @app.errorhandler(ValueError)
    def invalid_request(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(RuntimeError)
    def unavailable(exc):
        return jsonify(error=str(exc)), 409

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error=exc.description), exc.code

    def body(allowed):
        data = request.get_json()
        if not isinstance(data, dict) or set(data) - set(allowed):
            raise ValueError("Expected a JSON object with fields: " + ", ".join(allowed))
        return data

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        return jsonify(robot.status())

    @app.post("/api/command")
    def command():
        data = body(("command", "face"))
        if any(value is None for value in data.values()):
            raise ValueError("command and face cannot be null")
        robot.command(**data)
        return jsonify(status="ok", message="Command accepted")

    @app.post("/api/heartbeat")
    def heartbeat():
        body(())
        robot.heartbeat()
        return jsonify(status="ok")

    @app.get("/api/settings")
    def settings():
        return jsonify(robot.settings())

    @app.post("/api/settings")
    def update_settings():
        data = body(("frameDelay", "motorCurrentDelay"))
        current = robot.settings()
        robot.set_settings(data.get("frameDelay", current["frameDelay"]),
                           data.get("motorCurrentDelay", current["motorCurrentDelay"]))
        return jsonify(robot.settings())

    @app.get("/api/face")
    def face_preview():
        from io import BytesIO
        from PIL import Image
        from flask import send_file

        image = Image.frombytes("1", (128, 64), robot.faces.frame())
        output = BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        response = send_file(output, mimetype="image/png", max_age=0)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
