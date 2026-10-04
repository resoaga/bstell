from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from urllib.parse import quote

from . import audit
from .auth import SESSION_SECRET_KEY
from .database import Base, engine
from .routers import admin, api, site

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Bestellsystem")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY, same_site="lax")
app.add_middleware(GZipMiddleware, minimum_size=500)


class HeadAsGet:
    """Monitors, crawlers and link previews send HEAD requests; the routes only
    declare GET. Serve HEAD through the GET handler (the server drops the body)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "HEAD":
            scope = dict(scope, method="GET")
        await self.inner(scope, receive, send)


@app.middleware("http")
async def static_caching(request, call_next):
    """Long browser caching for files whose URL changes when the content changes
    (uploads have random names, fonts never change, style.css carries ?v=mtime)."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith(("/static/fonts/", "/static/uploads/", "/static/style.css", "/static/admin.css", "/static/admin.js")):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@app.middleware("http")
async def admin_audit(request, call_next):
    """Logs every successful admin change and hands the admin a short toast message
    (cookie read once by admin.js). Failed logins are logged too."""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/admin"):
        return response
    if response.status_code == 401 and request.headers.get("authorization"):
        audit.write(request, "Fehlgeschlagene Anmeldung", "Benutzername: " + audit.actor_of(request))
    elif request.method == "POST" and response.status_code < 400:
        action = audit.action_for(path[len("/admin"):])
        audit.write(request, action)
        response.set_cookie("admin_toast", quote(action), max_age=30, path="/admin", samesite="lax")
    return response


@app.middleware("http")
async def sandbox_uploaded_svg(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/uploads/") and request.url.path.endswith(".svg"):
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(site.router)
app.include_router(admin.router)
app.include_router(api.router)

app = HeadAsGet(app)
