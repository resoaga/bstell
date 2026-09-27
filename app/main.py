from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import SESSION_SECRET_KEY
from .database import Base, engine
from .routers import admin, api, site

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Bestellsystem")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY, same_site="lax")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(site.router)
app.include_router(admin.router)
app.include_router(api.router)
