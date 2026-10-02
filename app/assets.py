import os


def css_version() -> int:
    """File mtime of style.css, appended to its URL so browsers refetch after a deploy."""
    try:
        return int(os.path.getmtime("app/static/style.css"))
    except OSError:
        return 0
