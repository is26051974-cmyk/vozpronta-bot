from flask import Flask, request
import requests, os, tempfile, sqlite3, datetime, html

app = Flask(__name__)

PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "vozpronta123")
# ВЕЧНОЕ хранение: только если в Render добавлен Disk с Mount Path /data -> тогда в Env поставь DB_PATH=/data/vozpronta.db
# Без диска - работает локальный файл vozpronta.db, приложение НЕ падает
DB_PATH = os.environ.get("DB_PATH", "vozpronta.db")
# Turso (бесплатная вечная база, вместо диска в Render):
# В Render добавь: TURSO_DATABASE_URL=libsql://твоя-база-xxx.turso.io
#                  TURSO_AUTH_TOKEN=твой-токен
# Без них - работает как раньше, на локальном vozpronta.db
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
# Политика приватности: в Render поставь PUBLIC_URL=https://твой-домен.onrender.com
# Тогда ссылка в согласии будет правильной. Без Env - по умолчанию наш Render-домен.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://vozpronta-bot.onrender.com").rstrip("/")
PRIVACY_URL = os.environ.get("PRIVACY_URL", f"{PUBLIC_URL}/privacidad")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "sichgroupinvest@gmail.com")
# Админка: в Render поставь ADMIN_KEY=свой-сложный-пароль (не VERIFY_TOKEN!)
# Открывать: https://твой-домен.onrender.com/admin?key=ТВОЙ-ADMIN_KEY
# Без ADMIN_KEY в Env - админка закрыта (403), чтобы никто чужой не подсмотрел статистику.
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
WHATSAPP_PRIVACY_URL = "https://www.whatsapp.com/legal/privacy-policy"

CONSENT_TEXT = (
    "👋 ¡Hola! Soy VozPronta 10, un bot de transcripción.\n\n"
    "Funciono con la API oficial de WhatsApp (Meta).\n\n"
    "✅ Transcribo TU audio a texto, solo cuando TÚ me lo envías.\n"
    "✅ BORRO tu audio justo después de transcribirlo.\n"
    "⏰ Solo te escribo yo primero en UN caso: si TÚ mismo me pediste un recordatorio (por ejemplo, en tu audio dijiste \"mañana llamar a Juan\"). Te recuerdo solo esa tarea, nada más.\n"
    "❌ NUNCA te envío publicidad. Puedes apagar los recordatorios escribiendo SIN RECORDATORIOS.\n"
    "❌ NUNCA te pido contraseñas, códigos, tarjetas ni datos bancarios.\n"
    "❌ NUNCA envío publicidad ni reenvío tus mensajes a nadie.\n\n"
    "Guardo: tu contador, tu historial de transcripciones y tareas, tus recordatorios con fecha y tus favoritos (si los guardas con ⭐).\n"
    "Puedes borrar todo escribiendo BORRAR, o salir escribiendo NO.\n\n"
    "Nuestra prioridad es proteger tus datos. Puedes consultar cómo los tratamos en nuestra Política de Privacidad 👉 " + PRIVACY_URL + "\n"
    "y en la Política de privacidad de WhatsApp 👉 " + WHATSAPP_PRIVACY_URL + "\n\n"
    "¿Aceptas el uso del bot?"
)

# ---------- БАЗА: Turso (вечная) + fallback на локальный SQLite ----------
def _turso_http_url():
    u = TURSO_DATABASE_URL.strip()
    if u.startswith("libsql://"):
        u = "https://" + u[len("libsql://"):]
    return u.rstrip("/") + "/v2/pipeline"

def _turso_arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}

def _turso_query(sql, params=()):
    """Один запрос в Turso через HTTP. Возвращает список кортежей (строк)."""
    if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
        raise RuntimeError("Turso not configured")
    payload = {"requests": [
        {"type": "execute", "stmt": {"sql": sql, "args": [_turso_arg(v) for v in params]}},
        {"type": "close"},
    ]}
    r = requests.post(_turso_http_url(),
        headers={"Authorization": f"Bearer {TURSO_AUTH_TOKEN}", "Content-Type": "application/json"},
        json=payload, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Turso {r.status_code}: {r.text[:300]}")
    data = r.json()
    for res in data.get("results", []):
        if res.get("type") == "error":
            raise RuntimeError(f"Turso error: {res.get('error', {})}")
        if res.get("type") == "ok":
            result = res.get("response", {}).get("result", {})
            cols = [c["name"] for c in result.get("cols", [])]
            rows = []
            for row in result.get("rows", []):
                vals = []
                for cell in row:
                    if cell is None:
                        vals.append(None)
                    elif isinstance(cell, dict):
                        vals.append(cell.get("value"))
                    else:
                        vals.append(cell)
                rows.append(tuple(vals))
            return rows
    return []

def _turso_init():
    _turso_query("CREATE TABLE IF NOT EXISTS consent (user TEXT PRIMARY KEY, created_at TEXT)")
    _turso_query("CREATE TABLE IF NOT EXISTS stats (user TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last_at TEXT, last_tasks TEXT)")
    _turso_query("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, created_at TEXT, text TEXT, tasks TEXT)")
    _turso_query("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, due_date TEXT, task TEXT, sent INTEGER DEFAULT 0)")
    _turso_query("CREATE TABLE IF NOT EXISTS favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, created_at TEXT, text TEXT, tasks TEXT)")
    _turso_query("CREATE TABLE IF NOT EXISTS prefs (user TEXT PRIMARY KEY, reminders_on INTEGER DEFAULT 1)")

def _sqlite_conn():
    for path in [DB_PATH, "vozpronta.db", "/tmp/vozpronta.db"]:
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            conn = sqlite3.connect(path, timeout=10)
            conn.execute("CREATE TABLE IF NOT EXISTS consent (user TEXT PRIMARY KEY, created_at TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS stats (user TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last_at TEXT, last_tasks TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, created_at TEXT, text TEXT, tasks TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, due_date TEXT, task TEXT, sent INTEGER DEFAULT 0)")
            conn.execute("CREATE TABLE IF NOT EXISTS favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, created_at TEXT, text TEXT, tasks TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS prefs (user TEXT PRIMARY KEY, reminders_on INTEGER DEFAULT 1)")
            conn.commit()
            return conn
        except Exception as e:
            print(f"db fallback {path}: {e}")
            continue
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE IF NOT EXISTS consent (user TEXT PRIMARY KEY, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS stats (user TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last_at TEXT, last_tasks TEXT)")
    return conn

def db_query(sql, params=(), fetch="all"):
    """Единый запрос: сначала Turso, если он настроен. Если Turso упал - локальный SQLite.
    Возвращает: fetch='one' -> одна строка или None, fetch='all' -> список строк, fetch='none' -> None."""
    # Turso - основной
    if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
        try:
            _turso_init()
            rows = _turso_query(sql, params)
            if fetch == "one":
                return rows[0] if rows else None
            if fetch == "none":
                return None
            return rows
        except Exception as e:
            print(f"Turso failed, fallback to sqlite: {e}", flush=True)
    # Fallback - локальный SQLite, бот НЕ падает
    try:
        c = _sqlite_conn()
        cur = c.execute(sql, params)
        if fetch == "one":
            row = cur.fetchone()
        elif fetch == "none":
            row = None
        else:
            row = cur.fetchall()
        c.commit()
        c.close()
        return row
    except Exception as e:
        print(f"sqlite fallback failed: {e}", flush=True)
        if fetch == "one":
            return None
        if fetch == "none":
            return None
        return []

# Совместимость: старый код вызывал db().execute() - теперь это обёртка
class _DbCompat:
    def execute(self, sql, params=()):
        rows = db_query(sql, params, fetch="all")
        class _Cur:
            def __init__(self, rows): self._rows = rows
            def fetchone(self): return self._rows[0] if self._rows else None
            def fetchall(self): return self._rows
        return _Cur(rows)
    def commit(self): pass
    def close(self): pass

def db():
    return _DbCompat()

def has_consent(user):
    r = db_query("SELECT 1 FROM consent WHERE user=?", (user,), fetch="one")
    return bool(r)

def save_consent(user):
    db_query("INSERT OR REPLACE INTO consent VALUES (?,?)", (user, datetime.datetime.utcnow().isoformat()), fetch="none")

def remove_consent(user):
    db_query("DELETE FROM consent WHERE user=?", (user,), fetch="none")

def delete_all_data(user):
    # BORRAR: удаляем и согласие, и счётчик, и последние задачи - полностью
    db_query("DELETE FROM consent WHERE user=?", (user,), fetch="none")
    db_query("DELETE FROM stats WHERE user=?", (user,), fetch="none")
    db_query("DELETE FROM history WHERE user=?", (user,), fetch="none")
    db_query("DELETE FROM reminders WHERE user=?", (user,), fetch="none")
    db_query("DELETE FROM favorites WHERE user=?", (user,), fetch="none")
    db_query("DELETE FROM prefs WHERE user=?", (user,), fetch="none")

def bump_counter(user, tasks_text=""):
    row = db_query("SELECT count FROM stats WHERE user=?", (user,), fetch="one")
    n = (row[0] if row else 0) + 1
    db_query("INSERT INTO stats VALUES (?,?,?,?) ON CONFLICT(user) DO UPDATE SET count=excluded.count, last_at=excluded.last_at, last_tasks=excluded.last_tasks",
              (user, n, datetime.datetime.utcnow().isoformat(), tasks_text[:2000]), fetch="none")
    return n

def get_my_stats(user):
    return db_query("SELECT count, last_at, last_tasks FROM stats WHERE user=?", (user,), fetch="one")


# ---------- TOP-3: напоминания + монеты + resumen + BUSCAR + favoritos ----------
import re as _re
DAY_NAMES = {"lunes":0,"martes":1,"miércoles":2,"miercoles":2,"jueves":3,"viernes":4,"sábado":5,"sabado":5,"domingo":6,
             "segunda":0,"terça":1,"terca":1,"quarta":2,"quinta":3,"sexta":4,"sábado":5,"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
def _next_weekday(target, base):
    delta = (target - base.weekday()) % 7
    if delta == 0: delta = 7
    return base + datetime.timedelta(days=delta)
def parse_reminders(tasks_text, base_dt=None):
    """Ищет в задачах mañana / hoy / el lunes / 25 de noviembre и т.д. Возвращает [(due_date YYYY-MM-DD, task)]"""
    base = base_dt or datetime.datetime.utcnow()
    out = []
    for line in (tasks_text or "").splitlines():
        t = line.strip().lstrip("- ").strip()
        if not t or "sin tareas" in t.lower(): continue
        low = t.lower()
        due = None
        if "mañana" in low or "manana" in low or "amanhã" in low or "amanha" in low or "tomorrow" in low:
            due = (base + datetime.timedelta(days=1)).date()
        elif "hoy" in low or "hoje" in low or "today" in low:
            due = base.date()
        elif "pasado mañana" in low or "pasado manana" in low:
            due = (base + datetime.timedelta(days=2)).date()
        else:
            m = _re.search(r"(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)", low)
            months = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,"agosto":8,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12}
            if m:
                try:
                    y = base.year
                    d = datetime.date(y, months[m.group(2)], int(m.group(1)))
                    if d < base.date(): d = datetime.date(y+1, months[m.group(2)], int(m.group(1)))
                    due = d
                except Exception: pass
            if not due:
                for name, wd in DAY_NAMES.items():
                    if _re.search(r"\b(el\s+)?"+name+r"\b", low):
                        due = _next_weekday(wd, base).date(); break
        if due: out.append((due.isoformat(), t))
    return out
def save_history(user, text, tasks):
    db_query("INSERT INTO history (user, created_at, text, tasks) VALUES (?,?,?,?)", (user, datetime.datetime.utcnow().isoformat(), (text or "")[:4000], (tasks or "")[:2000]), fetch="none")
    for due, task in parse_reminders(tasks):
        db_query("INSERT INTO reminders (user, due_date, task, sent) VALUES (?,?,?,0)", (user, due, task), fetch="none")
def get_today_reminders(user):
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    return db_query("SELECT task FROM reminders WHERE user=? AND due_date=? AND sent=0", (user, today))
def mark_reminders_sent(user, today):
    db_query("UPDATE reminders SET sent=1 WHERE user=? AND due_date=?", (user, today), fetch="none")
def get_resumen(user):
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    rows = db_query("SELECT text, tasks FROM history WHERE user=? AND created_at LIKE ?", (user, today+"%"))
    if not rows: return ""
    lines = []
    for text, tasks in rows:
        for line in (tasks or "").splitlines():
            line=line.strip()
            if line and "sin tareas" not in line.lower() and line not in lines: lines.append(line)
    if not lines: return "Hoy no tienes tareas concretas. 🎉"
    return "\n".join(lines)
def search_history(user, q):
    rows = db_query("SELECT created_at, tasks FROM history WHERE user=? ORDER BY id DESC LIMIT 50", (user,))
    hits=[]
    for created_at, tasks in rows or []:
        if q.lower() in (tasks or "").lower():
            hits.append(f"• {str(created_at)[:10]}: {(tasks or '').replace(chr(10),' | ')[:220]}")
    return hits[:10]
def save_favorite(user):
    row = db_query("SELECT text, tasks FROM history WHERE user=? ORDER BY id DESC LIMIT 1", (user,), fetch="one")
    if not row: return False
    db_query("INSERT INTO favorites (user, created_at, text, tasks) VALUES (?,?,?,?)", (user, datetime.datetime.utcnow().isoformat(), row[0], row[1]), fetch="none")
    return True
def reminders_enabled(user):
    r = db_query("SELECT reminders_on FROM prefs WHERE user=?", (user,), fetch="one")
    return True if not r else bool(r[0])
def set_reminders(user, on):
    db_query("INSERT INTO prefs VALUES (?,?) ON CONFLICT(user) DO UPDATE SET reminders_on=excluded.reminders_on", (user, 1 if on else 0), fetch="none")
def get_favorites(user):
    return db_query("SELECT created_at, tasks FROM favorites WHERE user=? ORDER BY id DESC LIMIT 10", (user,))
# ---------- Выжимка задач из текста ----------
# ---------- Выжимка задач: gpt-oss-120b - качество, gpt-oss-20b - fallback ----------
TASKS_MODEL_PRIMARY = "openai/gpt-oss-120b"
TASKS_MODEL_FALLBACK = "openai/gpt-oss-20b"

TASKS_PROMPT = (
    "Eres un asistente que extrae SOLO tareas y acciones concretas. Responde SIEMPRE en el mismo idioma del texto (español / portugués / inglés). "
    "Devuelve una lista con vinetas, una tarea por linea, empezando cada linea con '- '. "
    "Reglas estrictas: "
    "1) Sin introducciones, sin saludos, sin repetir todo el texto. "
    "2) NUNCA inventes tareas, nombres, fechas, cifras o monedas que no esten en el texto. "
    "3) Conserva nombres, fechas, horas, cifras y monedas TAL CUAL, sin traducir ni redondear. "
    "4) Cada tarea = verbo en infinitivo + que + cuando/quien, si esta en el texto. "
    "5) Si es solo una opinion, recuerdo o comentario sin accion, NO es tarea. "
    "6) Maximo 10 tareas, las mas importantes primero. "
    "7) Si no hay ninguna tarea clara, devuelve EXACTAMENTE una linea: '- Sin tareas concretas.' "
    "Ejemplos: "
    "Texto: 'Mañana tengo que llamar a María a las 10 y comprar pan' -> "
    "'- Llamar a María mañana a las 10\n- Comprar pan' "
    "Texto: 'Me gusta mucho el café' -> '- Sin tareas concretas.'"
)

def _call_tasks_model(model, text):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.0,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": TASKS_PROMPT},
                {"role": "user", "content": text[:6000]}
            ]},
        timeout=30)
    print(f"tasks {model}: {r.status_code} {r.text[:500]}", flush=True)
    if r.status_code != 200:
        return ""
    return (r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()

def extract_tasks(text):
    """Выжимка задач: сначала gpt-oss-120b (качество), если упал - gpt-oss-20b (fallback)."""
    if not GROQ_KEY or not text.strip():
        return "- Sin tareas concretas."
    out = ""
    for model in (TASKS_MODEL_PRIMARY, TASKS_MODEL_FALLBACK):
        try:
            out = _call_tasks_model(model, text)
            if out:
                break
        except Exception as e:
            print(f"tasks error {model}: {e}", flush=True)
            continue
    if not out:
        return "- Sin tareas concretas."
    # Чистка: оставляем только строки "- ...", максимум 10, без мусора модели
    lines = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            lines.append(line)
        elif len(lines) == 0 and line.lower().startswith("sin tareas"):
            lines.append("- Sin tareas concretas.")
    if not lines:
        return "- Sin tareas concretas."
    return "\n".join(lines[:10])

@app.route('/')
def home():
    return "VozPronta 10 - Bot is Live!"

# ---------- Política de Privacidad - для Meta / WhatsApp Business + GDPR ----------
PRIVACY_HTML = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Política de Privacidad - VozPronta 10</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;line-height:1.6;color:#1a1a1a}}h1{{font-size:28px}}h2{{margin-top:28px}}a{{color:#0b6efd}}code{{background:#f2f2f2;padding:2px 6px;border-radius:4px}}</style>
</head><body>
<h1>🔒 Política de Privacidad - VozPronta 10</h1>
<p><b>Última actualización:</b> 11 de noviembre de 2026 - actualizada: recordatorios, resumen, búsqueda y favoritos</p>
<p>Nuestra prioridad es proteger tus datos. Esta política explica qué recogemos, para qué, cuánto lo guardamos y cómo puedes borrarlo.</p>
<h2>1. Quiénes somos</h2>
<p><b>VozPronta 10</b> es un bot de transcripción de audios de WhatsApp. Funciona con la API oficial de WhatsApp (Meta).</p>
<p>Contacto: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>
<h2>2. Qué datos recogemos</h2>
<ul>
<li><b>Tu número de WhatsApp</b> - para responderte y, solo si tú lo pediste, para enviarte tus recordatorios.</li>
<li><b>El texto de tus transcripciones y tus tareas</b> - para mostrarte tu contador, <code>MIS ESTADISTICAS</code>, <code>RESUMEN</code>, <code>BUSCAR</code> y tus favoritos.</li>
<li><b>Tus recordatorios con fecha</b> - solo los que TÚ creaste al decir una fecha en tu audio (por ejemplo, “mañana”, “el lunes”).</li>
<li><b>Tu contador de transcripciones y fecha de la última</b>.</li>
<li><b>Tu consentimiento</b> - fecha en la que pulsaste “Sí, acepto”.</li>
</ul>
<h2>3. Qué datos NO recogemos y NO guardamos</h2>
<ul>
<li><b>NO guardamos tus audios.</b> Tu audio se borra justo después de transcribirlo.</li>
<li>NO pedimos contraseñas, códigos, tarjetas ni datos bancarios.</li>
<li>NO enviamos publicidad ni reenviamos tus mensajes a nadie.</li>
<li>NO te escribimos primero para publicidad ni para ofrecerte nada. La única excepción son tus propios recordatorios.</li>
<li>NO vendemos tus datos.</li>
</ul>
<h2>4. Quién procesa tu audio</h2>
<p>Para transcribir usamos la API oficial de WhatsApp (Meta) y el servicio de transcripción Groq (Whisper) y de extracción de tareas Groq (gpt-oss). Tu audio se envía a Groq solo para transcribirlo y no se guarda después.</p>
<p>También se aplica la <a href="{WHATSAPP_PRIVACY_URL}">Política de privacidad de WhatsApp</a>.</p>
<h2>5. Cuánto guardamos tus datos</h2>
<p>Guardamos tu contador, tu historial de transcripciones y tareas, tus recordatorios y tus favoritos, hasta que tú pidas borrarlos. Tus audios no se guardan nunca.</p>
<h2>5b. Recordatorios - cómo funcionan y cómo apagarlos</h2>
<p>Si en tu audio dices una fecha (“mañana”, “el lunes”, “25 de noviembre”), guardamos esa tarea con esa fecha y ese día te enviamos un solo mensaje: “⏰ Hoy tienes: ...”.</p>
<p>Para apagarlos, escribe <code>SIN RECORDATORIOS</code>. Para volver a activarlos, escribe <code>CON RECORDATORIOS</code>. Para borrar TODO, escribe <code>BORRAR</code>.</p>
<h2>6. Tus derechos - cómo borrar todo</h2>
<ul>
<li>Escribe <code>BORRAR</code> en el chat - borramos TODO: consentimiento, contador y últimas tareas.</li>
<li>Escribe <code>NO</code> - retiramos tu consentimiento y ya no procesamos tus audios.</li>
<li>Escribe <code>SIN RECORDATORIOS</code> - dejamos de enviarte recordatorios, pero seguimos transcribiendo cuando tú nos escribes.</li>
<li>Escribe <code>PRIVACIDAD</code> - te enviamos esta política.</li>
<li>También puedes escribirnos a <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</li>
</ul>
<h2>7. Menores</h2>
<p>El bot no está dirigido a menores de 16 años.</p>
<h2>8. Cambios</h2>
<p>Si cambiamos esta política, publicaremos la nueva versión en esta misma página.</p>
<p><a href="/">← Volver a VozPronta 10</a></p>
</body></html>"""

@app.route('/privacidad')
@app.route('/politica-privacidad')
def privacidad():
    return PRIVACY_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

# ---------- Админка: только для тебя, по ADMIN_KEY ----------
def _mask_phone(user):
    # Показываем только последние 4 цифры, полный номер не светим даже в админке
    u = str(user or "")
    return "****" + u[-4:] if len(u) > 4 else "****"

@app.route('/admin')
def admin():
    key = request.args.get("key", "")
    if not ADMIN_KEY or key != ADMIN_KEY:
        return "403 - Acceso denegado. Falta ADMIN_KEY o clave incorrecta.", 403
    try:
        total_consent = (db_query("SELECT COUNT(*) FROM consent", fetch="one") or [0])[0]
        row = db_query("SELECT COUNT(*), COALESCE(SUM(count),0) FROM stats", fetch="one") or [0, 0]
        total_users, total_trans = row[0], row[1]
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        today_users = (db_query("SELECT COUNT(*) FROM stats WHERE last_at LIKE ?", (today + "%",), fetch="one") or [0])[0]
        users = db_query("SELECT user, count, last_at, last_tasks FROM stats ORDER BY last_at DESC LIMIT 50")
    except Exception as e:
        return f"Error admin: {e}", 500
    rows_html = ""
    for u, cnt, last_at, last_tasks in users:
        rows_html += (
            f"<tr><td>{html.escape(_mask_phone(u))}</td>"
            f"<td>{cnt}</td>"
            f"<td>{html.escape(str(last_at or '-'))}</td>"
            f"<td>{html.escape((last_tasks or '-')[:300])}</td></tr>"
        )
    if not rows_html:
        rows_html = "<tr><td colspan='4'>Aún no hay transcripciones.</td></tr>"
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin - VozPronta 10</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;line-height:1.5}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}}th{{background:#f2f2f2}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{border:1px solid #ddd;border-radius:10px;padding:14px 18px;min-width:160px}}.card b{{font-size:28px;display:block}}small{{color:#666}}</style>
</head><body>
<h1>📊 VozPronta 10 - Admin</h1>
<p><small>Teléfonos enmascarados (**** + últimas 4 cifras). No compartas esta URL con tu clave.</small></p>
<div class="cards">
<div class="card"><b>{total_consent}</b>Con consentimiento</div>
<div class="card"><b>{total_users}</b>Usuarios con transcripciones</div>
<div class="card"><b>{total_trans}</b>Transcripciones totales</div>
<div class="card"><b>{today_users}</b>Activos hoy (UTC)</div>
</div>
<h2>Últimos 50 usuarios</h2>
<table><tr><th>Usuario</th><th>Transcripciones</th><th>Última</th><th>Últimas tareas</th></tr>{rows_html}</table>
<p><a href="/privacidad">Política de Privacidad</a> · <a href="/">Inicio</a></p>
</body></html>""", 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route('/cron-reminders')
def cron_reminders():
    key = request.args.get("key", "")
    if not ADMIN_KEY or key != ADMIN_KEY: return "403", 403
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    rows = db_query("SELECT DISTINCT user FROM reminders WHERE due_date=? AND sent=0", (today,))
    sent = 0
    for (user,) in rows or []:
        if not reminders_enabled(user): continue
        tasks = get_today_reminders(user)
        if not tasks: continue
        if not has_consent(user): continue
        msg = "⏰ Hoy tienes:\n" + "\n".join(f"- {t}" for t in tasks)
        r = requests.post(f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages", headers={"Authorization": f"Bearer {TOKEN}"}, json={"messaging_product":"whatsapp","to":user,"text":{"body":msg[:3900]}})
        print(f"REMINDER to {user}: {r.status_code}", flush=True)
        if r.status_code in (200,201): mark_reminders_sent(user, today); sent += 1
    return f"OK sent={sent}", 200

@app.route('/webhook', methods=['GET'])

def verify():
    challenge = request.args.get("hub.challenge")
    token = request.args.get("hub.verify_token")
    if token == VERIFY_TOKEN and challenge:
        return challenge, 200
    if challenge:
        return challenge, 200
    return "ok", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print(f"POST WEBHOOK: {data}", flush=True)
        val = data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {})
        if 'messages' not in val:
            return "OK", 200
        m = val['messages'][0]
        frm = m['from']
        typ = m['type']

        def send_payload(payload):
            url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
            h = {"Authorization": f"Bearer {TOKEN}"}
            r = requests.post(url, headers=h, json=payload)
            print(f"SEND to {frm}: {r.status_code} {r.text[:500]}", flush=True)
            return r
        def send(t):
            return send_payload({"messaging_product":"whatsapp","to":frm,"text":{"body":t}})
        def send_consent():
            return send_payload({
                "messaging_product": "whatsapp", "to": frm, "type": "interactive",
                "interactive": {"type": "button", "body": {"text": CONSENT_TEXT},
                    "action": {"buttons": [
                        {"type": "reply", "reply": {"id": "consent_accept", "title": "✅ Sí, acepto"}},
                        {"type": "reply", "reply": {"id": "consent_decline", "title": "❌ No acepto"}}]}}
            })

        if typ == "interactive":
            btn_id = m.get('interactive', {}).get('button_reply', {}).get('id', '')
            if btn_id == "consent_accept":
                save_consent(frm)
                send("✅ ¡Gracias! Consentimiento aceptado.\n\n🎙️ Ahora envía un audio de hasta 10 min y te lo transcribo.")
            elif btn_id == "consent_decline":
                remove_consent(frm)
                send("❌ Entendido. No usaré el bot contigo.\nSi cambias de opinión, escribe \"Hola\" y vuelve a aceptar.")
            return "OK", 200

        if typ == "text":
            txt = m['text']['body'].strip()
            print(f"TEXT from {frm}: {txt}", flush=True)
            up = txt.upper()
            if up in ("BORRAR", "BORRAR MIS DATOS", "ELIMINAR MIS DATOS", "DELETE"):
                delete_all_data(frm)
                send("🗑️ Listo. Borré TODO: tu consentimiento, tu contador y tus últimas tareas.\nNo guardo nada tuyo.\nSi quieres volver, escribe \"Hola\".")
                return "OK", 200
            if up in ("PRIVACIDAD", "PRIVACY", "SEGURIDAD", "¿ES SEGURO?", "ES SEGURO"):
                send(f"🔒 Privacidad VozPronta 10:\n\nNuestra prioridad es proteger tus datos.\n\n• Uso la API oficial de WhatsApp (Meta).\n• Solo te escribo yo primero para tus propios recordatorios con fecha que TÚ me pediste. Apágalos con SIN RECORDATORIOS.\n• Tu audio se borra justo después de transcribirlo.\n• Nunca pido contraseñas, códigos ni tarjetas.\n• Nunca envío publicidad.\n• Para borrar todo escribe BORRAR, para salir escribe NO.\n\n📄 Nuestra Política de Privacidad: {PRIVACY_URL}\n📄 Política de WhatsApp: {WHATSAPP_PRIVACY_URL}")
                return "OK", 200
            if up in ("NO", "NO ACEPTO", "STOP", "BAJA"):
                remove_consent(frm)
                send("❌ Consentimiento retirado. Ya no procesaré tus audios.\nTus datos se pueden borrar por completo escribiendo BORRAR.\nEscribe \"Hola\" para volver a aceptar.")
                return "OK", 200
            # ⏰ Напоминания на сегодня - при каждом сообщении клиента проверяем, есть ли задачи на сегодня
            _today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            _due = get_today_reminders(frm) if reminders_enabled(frm) else []
            if _due:
                send("⏰ Hoy tienes:\n" + "\n".join(f"- {t}" for t in _due))
                mark_reminders_sent(frm, _today)
            if up in ("SIN RECORDATORIOS", "NO RECORDATORIOS", "APAGAR RECORDATORIOS", "SIN AVISOS"):
                set_reminders(frm, False)
                send("🔕 Recordatorios apagados. Ya no te escribiré yo primero.\nTus tareas siguen guardadas. Para activarlos de nuevo escribe CON RECORDATORIOS.")
                return "OK", 200
            if up in ("CON RECORDATORIOS", "ACTIVAR RECORDATORIOS", "RECORDATORIOS ON"):
                set_reminders(frm, True)
                send("🔔 Recordatorios activados. Te avisaré solo de las tareas con fecha que TÚ me pidas.")
                return "OK", 200
            if up.startswith("BUSCAR ") or up.startswith("BUSCA "):
                q = txt.split(" ",1)[1].strip() if " " in txt else ""
                hits = search_history(frm, q) if q else []
                send("🔎 Resultados para '" + q + "':\n" + ("\n".join(hits) if hits else "No encontré nada con esa palabra."))
                return "OK", 200
            if up in ("HOY", "MIS TAREAS", "TAREAS DE HOY", "HOY TENGO"):
                _rem = db_query("SELECT task FROM reminders WHERE user=? AND due_date=?", (frm, _today))
                send("⏰ Hoy tienes:\n" + ("\n".join(f"- {t[0]}" for t in _rem) if _rem else "Hoy no tienes tareas. 🎉"))
                return "OK", 200
            if up in ("RESUMEN", "RESUMEN DEL DIA", "RESUMEN DEL DÍA", "RESUMEN DE HOY", "PDF", "RESUMO"):
                send("📄 Resumen del día:\n" + get_resumen(frm))
                return "OK", 200
            if up in ("FAVORITO", "⭐", "FAVORITOS", "MIS FAVORITOS"):
                if up == "FAVORITO" or up == "⭐":
                    ok = save_favorite(frm)
                    send("⭐ Guardado en favoritos." if ok else "⭐ Aún no tienes transcripciones para guardar.")
                else:
                    favs = get_favorites(frm)
                    send("⭐ Favoritos:\n" + ("\n".join(f"• {str(c)[:10]}: {(t or '').replace(chr(10),' | ')[:200]}" for c,t in favs) if favs else "Aún no tienes favoritos. Escribe FAVORITO después de una transcripción importante."))
                return "OK", 200
            # 3. Счётчик - команда клиента
            if up in ("MIS ESTADISTICAS", "MIS ESTADÍSTICAS", "ESTADISTICAS", "ESTADÍSTICAS", "MI CONTADOR", "STATS"):
                row = get_my_stats(frm)
                if not row:
                    send("📊 Aún no tienes transcripciones.\nEnvía un audio y aquí aparecerá tu contador.")
                else:
                    send(f"📊 Tus estadísticas:\n• Transcripciones: {row[0]}\n• Última: {row[1][:16] if row[1] else '-'}\n\n📋 Últimas tareas:\n{row[2] or '• Sin tareas concretas.'}")
                return "OK", 200
            if not has_consent(frm):
                send_consent()
                return "OK", 200
            send("🎙️ VozPronta 10 - ¡Envía un audio de hasta 10 min!\n\n⏰ HOY - tareas de hoy  •  📄 RESUMEN - resumen del día  •  🔎 BUSCAR Juan  •  ⭐ FAVORITO")
            return "OK", 200

        if typ in ("audio","voice"):
            if not has_consent(frm):
                send_consent()
                send("⚠️ Primero pulsa \"✅ Sí, acepto\" arriba y vuelve a enviar el audio.")
                return "OK", 200
            aid = m['audio']['id']
            info = requests.get(f"https://graph.facebook.com/v19.0/{aid}", headers={"Authorization": f"Bearer {TOKEN}"}).json()
            if 'url' not in info:
                send("❌ Error al descargar el audio")
                return "OK", 200
            aud = requests.get(info['url'], headers={"Authorization": f"Bearer {TOKEN}"}).content
            print(f"AUDIO download: {len(aud)} bytes", flush=True)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tf.write(aud); path = tf.name
            with open(path, 'rb') as f:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                  headers={"Authorization": f"Bearer {GROQ_KEY}"},
                                  files={"file": f},
                                  data={"model":"whisper-large-v3"})
            os.unlink(path)
            print(f"GROQ whisper: {r.status_code} {r.text[:300]}", flush=True)
            if r.status_code == 200:
                text = r.json().get('text','')
                # Задачи и база НЕ должны убивать отправку транскрипции - каждую в свой try
                try:
                    tasks = extract_tasks(text)
                except Exception as e:
                    print(f"tasks failed, continue without tasks: {e}", flush=True)
                    tasks = "- Sin tareas concretas."
                try:
                    save_history(frm, text, tasks)
                except Exception as e:
                    print(f"save_history failed, continue: {e}", flush=True)
                try:
                    n = bump_counter(frm, tasks)
                except Exception as e:
                    print(f"bump_counter failed, continue: {e}", flush=True)
                    n = 0
                msg = f"✅ Transcripción:\n\n{text}"
                msg += f"\n\n📋 Tareas:\n{tasks or '- Sin tareas concretas.'}"
                if n:
                    msg += f"\n\n📊 Esta es tu transcripción #{n}."
                send(msg[:3900])
            else:
                send(f"❌ Error transcripción (Groq {r.status_code}): {r.text[:300]}")
    except Exception as e:
        print(e)
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
