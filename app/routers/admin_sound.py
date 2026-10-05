"""Einstellungen → Benachrichtigung: Klingelton für neue Bestellungen."""
import os
import uuid
from urllib.parse import quote_plus

from fastapi import Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import sounds, zones as zones_lib
from ..database import get_db
from ..models import CustomSound

UPLOAD_DIR = "app/static/uploads"
ALLOWED = {".mp3", ".wav", ".ogg", ".m4a"}
MAX_BYTES = 1_000_000


def _back(error: str = ""):
    url = "/admin/settings/mitteilungen"
    if error:
        url += "?sound_error=" + quote_plus(error)
    return RedirectResponse(url=url + "#sound", status_code=303)


def setup(router, templates, mutating, views):
    def sound_view(request: Request, db: Session):
        prefs = sounds.get_prefs(db)
        return templates.TemplateResponse(
            "admin/settings_sound.html",
            {
                "request": request, "prefs": prefs, "builtins": sounds.BUILTINS,
                "customs": db.query(CustomSound).order_by(CustomSound.id).all(),
                "zone_list": zones_lib.load_zones(db), "error": request.query_params.get("sound_error", ""),
            },
        )

    views["sound"] = sound_view

    @router.post("/settings/sound", dependencies=mutating)
    async def save_sound(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        for key, low, high in (("ring_seconds", 1, 60), ("pause_seconds", 1, 120), ("volume", 0, 100)):
            raw = (form.get(key) or "").strip()
            if raw.isdigit():
                sounds.set_pref(db, key, str(max(low, min(high, int(raw)))))
        valid = {"n"} | {f"b:{k}" for k, _ in sounds.BUILTINS} | {f"f:{c.id}" for c in db.query(CustomSound).all()}
        for key in ["default", "pickup"] + [f"zone_{z.id}" for z in zones_lib.load_zones(db)]:
            value = (form.get("sound_" + key) or "").strip()
            sounds.set_pref(db, key, value if value in valid else ("b:klingel" if key == "default" else ""))
        db.commit()
        return _back()

    @router.post("/settings/sound/upload", dependencies=mutating)
    async def upload_sound(name: str = Form(""), file: UploadFile = File(...), db: Session = Depends(get_db)):
        ext = os.path.splitext(file.filename or "")[1].lower()
        data = await file.read()
        if ext not in ALLOWED:
            return _back("Erlaubt sind mp3, wav, ogg und m4a.")
        if not data or len(data) > MAX_BYTES:
            return _back("Die Datei ist leer oder grösser als 1 MB. Der Ton darf kurz sein (wenige Sekunden).")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename = f"sound-{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(data)
        label = name.strip()[:40] or os.path.splitext(os.path.basename(file.filename or "Ton"))[0][:40]
        db.add(CustomSound(name=label, filename=filename))
        db.commit()
        return _back()

    @router.post("/settings/sound/{sound_id}/delete", dependencies=mutating)
    def delete_sound(sound_id: int, db: Session = Depends(get_db)):
        row = db.get(CustomSound, sound_id)
        if row is not None:
            try:
                os.remove(os.path.join(UPLOAD_DIR, os.path.basename(row.filename)))
            except OSError:
                pass
            db.delete(row)
            db.commit()
        return _back()
