from routes.main import main_bp
from routes.health import health_bp
from routes.api import api_bp
from routes.files import files_bp


def register_blueprints(app):
    """Register all blueprints with the Flask app."""
    app.register_blueprint(main_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(files_bp)
