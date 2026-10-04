import os


def css_version() -> int:
    """File mtime of style.css, appended to its URL so browsers refetch after a deploy."""
    try:
        return int(os.path.getmtime("app/static/style.css"))
    except OSError:
        return 0


def admin_version() -> int:
    """Newest mtime of the admin CSS/JS, so browsers refetch them after a deploy."""
    try:
        return int(max(os.path.getmtime("app/static/admin.css"), os.path.getmtime("app/static/admin.js")))
    except OSError:
        return 0
