"""Path bootstrap: make the repository root (which holds templates/ and static/)
the Flask app root, so resource lookup works when the app package lives inside it."""
import os


def configure_app_paths(app):
    repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__))))
    app.root_path = repo_root
    app.template_folder = "templates"
    app.static_folder = "static"
    app.config["SQLALCHEMY_DATABASE_URI"] = _resolve_db_uri(
        app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///civicsync.db"), repo_root)
    return app


def _resolve_db_uri(uri, repo_root):
    """Make relative sqlite:/// paths absolute under the repo root so the working
    directory from which the server is started does not matter."""
    prefix = "sqlite:///"
    if uri == "sqlite:///:memory:" or uri.startswith("sqlite:///:memory:?"):
        return uri
    if uri.startswith(prefix) and not os.path.isabs(uri[len(prefix):]):
        return prefix + os.path.join(repo_root, uri[len(prefix):]).replace("\\", "/")
    return uri
