#!/bin/sh
# Re-apply patches after container recreation; restart only AppDaemon.
set -eu
C=${PETLIBRO_CONTAINER:-app_dfcf7f92_petlibro_local}
PATCHDIR=/mnt/data/supervisor/share/petlibro-patches
PY=/opt/appdaemon-venv/bin/python3
PENDING=/data/.petlibro-patch-restart-pending

# Serialize scheduled invocations; the kernel releases this lock after a crash.
exec 9>"$PATCHDIR/.fix-addon.lock"
flock -n 9 || { rc=$?; [ "$rc" = 1 ] && exit 0; exit "$rc"; }
[ -s "$PATCHDIR/tower.env" ] || { echo "ERROR: missing $PATCHDIR/tower.env"; exit 1; }
docker exec "$C" true
if ! docker exec -i "$C" cmp -s /data/tower.env - < "$PATCHDIR/tower.env"; then
  docker exec -i "$C" sh -c 'umask 077; cat > /data/tower.env; touch /data/.server-aid-restart-pending' < "$PATCHDIR/tower.env"
fi
docker exec "$C" sh -c 'command -v pgrep >/dev/null && command -v pkill >/dev/null'

if ! docker exec "$C" grep -q "LOCAL BINDING PATCH" /opt/petlibro-local/appdaemon/mqtt_client.py; then
  docker exec "$C" touch "$PENDING"
  docker exec -i "$C" "$PY" - < "$PATCHDIR/binding_patch.py"
  echo "$(date '+%F %T') binding patch applied"
fi

if ! docker exec "$C" grep -q '^[[:space:]]*- homeassistant/status[[:space:]]*$' /opt/petlibro-local/templates/appdaemon.yaml.tmpl; then
  docker exec "$C" touch "$PENDING"
  docker exec -i "$C" "$PY" - <<'PY'
from pathlib import Path
import shutil
p = Path('/opt/petlibro-local/templates/appdaemon.yaml.tmpl')
src = p.read_text()
anchor = '        - petlibro_local/discovery/refresh'
if src.count(anchor) != 1:
    raise RuntimeError('expected exactly one client_topics anchor')
tmp = p.with_suffix('.tmp')
try:
    tmp.write_text(src.replace(anchor, anchor + '\n        - homeassistant/status', 1))
    shutil.copymode(p, tmp)
    tmp.replace(p)
finally:
    tmp.unlink(missing_ok=True)
PY
  echo "$(date '+%F %T') template patched"
fi

if ! docker exec "$C" grep -q '^[[:space:]]*- homeassistant/status[[:space:]]*$' /data/appdaemon.yaml; then
  docker exec "$C" touch "$PENDING"
fi
if docker exec "$C" test -f "$PENDING"; then
  docker exec "$C" /usr/local/bin/petlibro-render-config appdaemon
  docker exec "$C" grep -q '^[[:space:]]*- homeassistant/status[[:space:]]*$' /data/appdaemon.yaml
  docker exec "$C" s6-svc -r /run/service/appdaemon
  docker exec "$C" rm "$PENDING"
  echo "$(date '+%F %T') rendered + appdaemon restart requested"
fi

# Exact argv matching cannot match this shell or the pgrep command itself.
PATTERN='^/opt/appdaemon-venv/bin/python3 /data/server_aid_daemon.py$'
if ! docker exec -i "$C" cmp -s /data/server_aid_daemon.py - < "$PATCHDIR/server_aid_daemon.py"; then
  docker exec -i "$C" "$PY" -c '
from pathlib import Path
import sys
p = Path("/data/server_aid_daemon.py")
src = sys.stdin.read()
compile(src, str(p), "exec")
tmp = p.with_suffix(".tmp")
try:
    tmp.write_text(src)
    Path("/data/.server-aid-restart-pending").touch()
    tmp.replace(p)
finally:
    tmp.unlink(missing_ok=True)
' < "$PATCHDIR/server_aid_daemon.py"
fi
if docker exec "$C" test -f /data/.server-aid-restart-pending; then
  docker exec "$C" sh -c '
    pkill -f "$1" || [ "$?" = 1 ] || exit "$?"
    n=0
    while pgrep -f "$1" >/dev/null; do
      n=$((n + 1))
      [ "$n" -lt 10 ] || exit 1
      sleep 1
    done
  ' sh "$PATTERN"
fi
if ! docker exec "$C" pgrep -f "$PATTERN" >/dev/null; then
  docker exec "$C" sh -c 'set -a; . /data/tower.env; set +a; nohup /opt/appdaemon-venv/bin/python3 /data/server_aid_daemon.py >> /data/petlibro_server_aid.log 2>&1 < /dev/null &'
  sleep 1
  docker exec "$C" pgrep -f "$PATTERN" >/dev/null
  docker exec "$C" rm -f /data/.server-aid-restart-pending
  echo "$(date '+%F %T') server-aid daemon launched"
fi
