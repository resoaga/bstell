from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .routers import admin, orders

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Bestellsystem Admin")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(orders.router)
app.include_router(admin.router)


@app.get("/")
def root():
    return RedirectResponse(url="/admin/menu")
