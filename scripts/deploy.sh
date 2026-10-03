#!/bin/sh
# Auf dem Server nach "Pull Updates" in Plesk ausfuehren:
#     sh ~/httpdocs/scripts/deploy.sh
# Fuehrt die (wiederholbaren) DB-Migrationen aus, startet bstell neu und prueft,
# ob die Seite antwortet.
set -e
cd "$(dirname "$0")/.."

echo "== Python-Pakete (Pillow fuer Bildverkleinerung) =="
python3 -m pip install --user -q "Pillow>=10,<12" "fpdf2>=2.7,<3" || echo "Hinweis: pip-Installation fehlgeschlagen - Bilder werden dann unverkleinert gespeichert"

python3 -c "import PIL; print('Pillow', PIL.__version__, 'OK')" || echo "Pillow nicht importierbar - bitte melden"
python3 -c "import fpdf; print('fpdf2', fpdf.__version__, 'OK')" || echo "fpdf2 nicht importierbar - PDF-Beleg ist dann aus, Rest laeuft"

echo "== DB-Migrationen =="
python3 scripts/migrate_option_library.py || true
python3 scripts/add_columns.py || true
python3 scripts/upgrade_sauce.py || true

echo "== Neustart =="
pkill -f "uvicorn app.main:app" || true
sleep 2
sh "$HOME/start_bestellsystem.sh"
sleep 4

echo "== Pruefung =="
code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8811/ || true)
if [ "$code" = "200" ]; then
  echo "OK: bstell laeuft (HTTP 200)"
else
  echo "FEHLER: Startseite antwortet mit '$code' - bitte melden"
fi
