#!/bin/sh
# Auf dem Server nach "Pull Updates" in Plesk ausfuehren:
#     sh ~/httpdocs/scripts/deploy.sh
# Fuehrt die (wiederholbaren) DB-Migrationen aus, startet bstell neu und prueft,
# ob die Seite antwortet.
set -e
cd "$(dirname "$0")/.."

echo "== Python-Pakete (Pillow fuer Bilder, fpdf2 fuer PDF-Beleg) =="
# Das Python nehmen, mit dem die laufende App gestartet wurde (kann ein venv sein),
# nicht blind "python3": sonst landen die Pakete woanders und die App findet sie nicht.
APP_PY=""
for PID in $(pgrep -f "uvicorn app.main:app"); do
  [ -r "/proc/$PID/cmdline" ] || continue
  FIRST=$(tr '\0' '\n' < "/proc/$PID/cmdline" | head -1)
  case "$(basename "$FIRST")" in
    python*)
      case "$FIRST" in
        /*) APP_PY="$FIRST" ;;
        *)  APP_PY=$(command -v "$FIRST" 2>/dev/null || true) ;;
      esac
      break ;;
  esac
done
[ -n "$APP_PY" ] && [ -x "$APP_PY" ] || APP_PY="python3"
echo "App-Python: $APP_PY"
if "$APP_PY" -c "import sys; sys.exit(0 if sys.prefix == sys.base_prefix else 1)"; then USERFLAG="--user"; else USERFLAG=""; fi
"$APP_PY" -m pip install $USERFLAG -q "Pillow>=10,<12" "fpdf2>=2.7,<3" || echo "Hinweis: pip-Installation fehlgeschlagen - dann gibt es keine Bildverkleinerung/kein PDF"
"$APP_PY" -c "import PIL; print('Pillow', PIL.__version__, 'OK')" || echo "Pillow im App-Python nicht importierbar - bitte melden"
"$APP_PY" -c "import fpdf; print('fpdf2', fpdf.__version__, 'OK')" || echo "fpdf2 im App-Python nicht importierbar - PDF-Beleg ist dann aus, Rest laeuft"

echo "== DB-Migrationen =="
python3 scripts/migrate_option_library.py || true
python3 scripts/add_columns.py || true
python3 scripts/migrate_zones.py || true
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
