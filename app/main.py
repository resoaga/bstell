from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import SESSION_SECRET_KEY
from .database import Base, engine
from .routers import admin, api, site

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Bestellsystem")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY, same_site="lax")
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def static_caching(request, call_next):
    """Long browser caching for files whose URL changes when the content changes
    (uploads have random names, fonts never change, style.css carries ?v=mtime)."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith(("/static/fonts/", "/static/uploads/", "/static/style.css")):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=604800"
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
