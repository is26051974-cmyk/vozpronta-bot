from flask import Flask, request
import requests, os, tempfile, sqlite3, datetime, html, re as _re

app = Flask(__name__)
PHONE_ID = os.environ.get("PHONE_ID", "1345713805299738")
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "vozpronta123")
DB_PATH = os.environ.get("DB_PATH", "vozpronta.db")
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://vozpronta-bot.onrender.com").rstrip("/")
PRIVACY_URL = os.environ.get("PRIVACY_URL", f"{PUBLIC_URL}/privacidad")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "sichgroupinvest@gmail.com")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
WHATSAPP_PRIVACY_URL = "https://www.whatsapp.com/legal/privacy-policy"
# СКОРОСТЬ: turbo-модель в ~3-4 раза быстрее whisper-large-v3, качество то же
WHISPER_MODEL = "whisper-large-v3-turbo"
TASKS_MODEL = "llama-3.1-8b-instant"

CONSENT_TEXT = (
    "👋 ¡Hola! Soy VozPronta 10, un bot de transcripción.\n\n"
    "Funciono con la API oficial de WhatsApp (Meta).\n\n"
    "✅ Transcribo TU audio a texto, solo cuando TÚ me lo envías.\n"
    "✅ BORRO tu audio justo después de transcribirlo.\n"
    "⏰ Solo te escribo yo primero en UN caso: si TÚ mismo me pediste un recordatorio (por ejemplo, en tu audio dijiste \"mañana llamar a Juan\"). Te recuerdo solo esa tarea, nada más.\n"
    "❌ NUNCA te envío publicidad. Puedes apagar los recordatorios escribiendo SIN RECORDATORIOS.\n"
    "❌ NUNCA te pido contraseñas, códigos, tarjetas ni datos bancarios.\n\n"
    "Guardo: tu contador, tu historial de transcripciones y tareas, tus recordatorios con fecha y tus favoritos (si los guardas con ⭐).\n"
    "Puedes borrar todo escribiendo BORRAR, o salir escribiendo NO.\n\n"
    "Política de Privacidad 👉 " + PRIVACY_URL + "\n"
    "Política de WhatsApp 👉 " + WHATSAPP_PRIVACY_URL + "\n\n"
    "¿Aceptas el uso del bot?"
)

# ---------- БАЗА: Turso + fallback SQLite ----------
def _turso_http_url():
    u = TURSO_DATABASE_URL
    if u.startswith("libsql://"): u = "https://" + u[len("libsql://"):]
    return u.rstrip("/") + "/v2/pipeline"
def _turso_arg(v):
    if v is None: return {"type": "null"}
    if isinstance(v, bool): return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int): return {"type": "integer", "value": str(v)}
    if isinstance(v, float): return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}
def _turso_query(sql, params=()):
    if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN: raise RuntimeError("Turso not configured")
    payload = {"requests": [{"type": "execute", "stmt": {"sql": sql, "args": [_turso_arg(v) for v in params]}}, {"type": "close"}]}
    r = requests.post(_turso_http_url(), headers={"Authorization": f"Bearer {TURSO_AUTH_TOKEN}", "Content-Type": "application/json"}, json=payload, timeout=10)
    if r.status_code != 200: raise RuntimeError(f"Turso {r.status_code}: {r.text[:200]}")
    data = r.json()
    for res in data.get("results", []):
        if res.get("type") == "error": raise RuntimeError(f"Turso error: {res.get('error', {})}")
        if res.get("type") == "ok":
            result = res.get("response", {}).get("result", {})
            rows = []
            for row in result.get("rows", []):
                vals = []
                for cell in row:
                    if cell is None: vals.append(None)
                    elif isinstance(cell, dict): vals.append(cell.get("value"))
                    else: vals.append(cell)
                rows.append(tuple(vals))
            return rows
    return []
def _sqlite_conn():
    for path in [DB_PATH, "vozpronta.db", "/tmp/vozpronta.db"]:
        try:
            d = os.path.dirname(path)
            if d: os.makedirs(d, exist_ok=True)
            conn = sqlite3.connect(path, timeout=10)
            for tbl in ["consent (user TEXT PRIMARY KEY, created_at TEXT)",
                        "stats (user TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last_at TEXT, last_tasks TEXT)",
                        "history (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, created_at TEXT, text TEXT, tasks TEXT)",
                        "reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, due_date TEXT, task TEXT, sent INTEGER DEFAULT 0)",
                        "favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, created_at TEXT, text TEXT, tasks TEXT)",
                        "prefs (user TEXT PRIMARY KEY, reminders_on INTEGER DEFAULT 1)"]:
                conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}")
            conn.commit()
            return conn
        except Exception: continue
    raise RuntimeError("No DB")
def db_query(sql, params=(), fetch=True):
    # Сначала Turso, если настроен, иначе SQLite
    if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
        try: return _turso_query(sql, params)
        except Exception as e: print(f"Turso fail, fallback sqlite: {e}", flush=True)
    conn = _sqlite_conn()
    cur = conn.cursor(); cur.execute(sql, params)
    rows = cur.fetchall() if fetch else []
    conn.commit(); conn.close()
    return rows
def db_execute(sql, params=()):
    return db_query(sql, params, fetch=False)

# ---------- Согласие / настройки ----------
def has_consent(user):
    rows = db_query("SELECT user FROM consent WHERE user=?", (user,))
    return bool(rows)
def save_consent(user):
    db_execute("INSERT OR REPLACE INTO consent (user, created_at) VALUES (?, ?)", (user, datetime.datetime.utcnow().isoformat()))
def remove_consent(user):
    db_execute("DELETE FROM consent WHERE user=?", (user,))
def delete_all_data(user):
    for t in ["consent","stats","history","reminders","favorites","prefs"]:
        db_execute(f"DELETE FROM {t} WHERE user=?", (user,))
def reminders_enabled(user):
    rows = db_query("SELECT reminders_on FROM prefs WHERE user=?", (user,))
    return True if not rows else bool(rows[0][0])
def set_reminders(user, on):
    db_execute("INSERT OR REPLACE INTO prefs (user, reminders_on) VALUES (?, ?)", (user, 1 if on else 0))
def get_today_reminders(user):
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    rows = db_query("SELECT task FROM reminders WHERE user=? AND due_date=? AND sent=0", (user, today))
    return [r[0] for r in (rows or [])]
def mark_reminders_sent(user, today):
    db_execute("UPDATE reminders SET sent=1 WHERE user=? AND due_date=?", (user, today))

# ---------- История / счётчик / фавориты ----------
def save_history(user, text, tasks):
    db_execute("INSERT INTO history (user, created_at, text, tasks) VALUES (?, ?, ?, ?)", (user, datetime.datetime.utcnow().isoformat(), text, tasks))
def bump_counter(user, tasks):
    """Возвращает новый номер транскрипции. СЧЁТЧИК - всегда, даже если история упала."""
    rows = db_query("SELECT count FROM stats WHERE user=?", (user,))
    n = (rows[0][0] if rows else 0) + 1
    db_execute("INSERT OR REPLACE INTO stats (user, count, last_at, last_tasks) VALUES (?, ?, ?, ?)", (user, n, datetime.datetime.utcnow().isoformat(), tasks))
    return n
def get_my_stats(user):
    rows = db_query("SELECT count, last_at, last_tasks FROM stats WHERE user=?", (user,))
    return rows[0] if rows else None
def get_resumen(user):
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    rows = db_query("SELECT text, tasks FROM history WHERE user=? AND created_at LIKE ?", (user, today + "%"))
    if not rows: return "Hoy aún no tienes transcripciones."
    out = []
    for t, tasks in rows[-10:]:
        out.append(f"• {t[:200]}")
        if tasks and "Sin tareas" not in tasks: out.append(f"  📋 {tasks[:200]}")
    return "\n".join(out) or "Hoy aún no tienes transcripciones."
def search_history(user, q):
    rows = db_query("SELECT created_at, text FROM history WHERE user=? AND text LIKE ? ORDER BY id DESC LIMIT 5", (user, f"%{q}%"))
    return [f"• {str(c)[:10]}: {t[:200]}" for c, t in (rows or [])]
def save_favorite(user):
    rows = db_query("SELECT created_at, text, tasks FROM history WHERE user=? ORDER BY id DESC LIMIT 1", (user,))
    if not rows: return False
    c, t, tasks = rows[0]
    db_execute("INSERT INTO favorites (user, created_at, text, tasks) VALUES (?, ?, ?, ?)", (user, c, t, tasks))
    return True
def get_favorites(user):
    return db_query("SELECT created_at, tasks FROM favorites WHERE user=? ORDER BY id DESC LIMIT 10", (user,)) or []

# ---------- Выжимка задач (быстрая модель, мало токенов) ----------
def extract_tasks(text):
    if not GROQ_KEY: return "- Sin tareas concretas."
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": TASKS_MODEL, "temperature": 0.1, "max_tokens": 250,
                  "messages": [{"role": "system", "content": "Extrae solo tareas concretas con fecha si la hay. Lista corta con guiones. Si no hay tareas, responde exactamente: - Sin tareas concretas. NUNCA inventes tareas, nombres, fechas o cifras."},
                               {"role": "user", "content": text[:1500]}]}, timeout=20)
        if r.status_code != 200: return "- Sin tareas concretas."
        return (r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip() or "- Sin tareas concretas."
    except Exception as e:
        print(f"tasks error: {e}", flush=True); return "- Sin tareas concretas."

@app.route('/')
def home():
    return "VozPronta 10 - Bot is running! Use /webhook for WhatsApp"
@app.route('/privacidad')
def privacidad():
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Política de Privacidad - VozPronta 10</title></head>
<body><h1>Política de Privacidad - VozPronta 10</h1>
<p>Transcribimos tu audio a texto solo cuando TÚ lo envías. Borramos el audio justo después de transcribirlo.</p>
<p>Guardamos: contador, historial de transcripciones y tareas, recordatorios con fecha y favoritos.</p>
<p>Para borrar todo escribe BORRAR. Para salir escribe NO. Contacto: {html.escape(CONTACT_EMAIL)}</p>
</body></html>""", 200, {"Content-Type": "text/html; charset=utf-8"}
@app.route('/admin')
def admin():
    if not ADMIN_KEY or request.args.get("key", "") != ADMIN_KEY: return "403", 403
    rows = db_query("SELECT count(*) FROM stats") or [(0,)]
    total = rows[0][0] if rows else 0
    users = db_query("SELECT user, count, last_at, last_tasks FROM stats ORDER BY last_at DESC LIMIT 50") or []
    rows_html = "".join(f"<tr><td>{html.escape(str(u))}</td><td>{c}</td><td>{html.escape(str(l or ''))}</td><td>{html.escape(str(t or '')[:200])}</td></tr>" for u, c, l, t in users)
    return f"<html><body><h1>VozPronta Admin</h1><p>Transcripciones totales: <b>{total}</b></p><table border=1><tr><th>Usuario</th><th>Transcripciones</th><th>Última</th><th>Últimas tareas</th></tr>{rows_html}</table></body></html>", 200, {"Content-Type": "text/html; charset=utf-8"}
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
        if not tasks or not has_consent(user): continue
        msg = "⏰ Hoy tienes:\n" + "\n".join(f"- {t}" for t in tasks)
        r = requests.post(f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages", headers={"Authorization": f"Bearer {TOKEN}"}, json={"messaging_product": "whatsapp", "to": user, "text": {"body": msg[:3900]}})
        print(f"REMINDER to {user}: {r.status_code}", flush=True)
        if r.status_code in (200, 201): mark_reminders_sent(user, today); sent += 1
    return f"OK sent={sent}", 200

@app.route('/webhook', methods=['GET'])
def verify():
    challenge = request.args.get("hub.challenge")
    token = request.args.get("hub.verify_token")
    if token == VERIFY_TOKEN and challenge: return challenge, 200
    if challenge: return challenge, 200
    return "ok", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print(f"POST WEBHOOK: {data}", flush=True)
        val = data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {})
        if 'messages' not in val: return "OK", 200
        m = val['messages'][0]; frm = m['from']; typ = m['type']
        def send_payload(payload):
            url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
            r = requests.post(url, headers={"Authorization": f"Bearer {TOKEN}"}, json=payload)
            print(f"SEND to {frm}: {r.status_code} {r.text[:500]}", flush=True)
            return r
        def send(t):
            return send_payload({"messaging_product": "whatsapp", "to": frm, "text": {"body": t}})
        def send_consent():
            # FIX 131009: interactive body max 1024 символа, полный текст - отдельным сообщением
            send(CONSENT_TEXT)
            return send_payload({"messaging_product": "whatsapp", "to": frm, "type": "interactive",
                "interactive": {"type": "button", "body": {"text": "👋 Soy VozPronta 10. Transcribo tu audio a texto y borro el audio después. ¿Aceptas el uso del bot?"},
                    "action": {"buttons": [{"type": "reply", "reply": {"id": "consent_accept", "title": "✅ Sí, acepto"}}, {"type": "reply", "reply": {"id": "consent_decline", "title": "❌ No acepto"}}]}}})
        if typ == "interactive":
            btn_id = m.get('interactive', {}).get('button_reply', {}).get('id', '')
            if btn_id == "consent_accept":
                save_consent(frm); send("✅ ¡Gracias! Consentimiento aceptado.\n\n🎙️ Ahora envía un audio de hasta 10 min y te lo transcribo.")
            elif btn_id == "consent_decline":
                remove_consent(frm); send("❌ Entendido. No usaré el bot contigo.\nSi cambias de opinión, escribe \"Hola\" y vuelve a aceptar.")
            return "OK", 200
        if typ == "text":
            txt = m['text']['body'].strip(); print(f"TEXT from {frm}: {txt}", flush=True); up = txt.upper()
            if up in ("BORRAR", "BORRAR MIS DATOS", "ELIMINAR MIS DATOS", "DELETE"):
                delete_all_data(frm); send("🗑️ Listo. Borré TODO: tu consentimiento, tu contador y tus últimas tareas.\nNo guardo nada tuyo.\nSi quieres volver, escribe \"Hola\"."); return "OK", 200
            if up in ("PRIVACIDAD", "PRIVACY", "SEGURIDAD", "¿ES SEGURO?", "ES SEGURO"):
                send(f"🔒 Privacidad VozPronta 10:\n\n• Uso la API oficial de WhatsApp (Meta).\n• Tu audio se borra justo después de transcribirlo.\n• Nunca pido contraseñas, códigos ni tarjetas.\n• Nunca envío publicidad.\n• Para borrar todo escribe BORRAR, para salir escribe NO.\n\n📄 Política: {PRIVACY_URL}\n📄 WhatsApp: {WHATSAPP_PRIVACY_URL}"); return "OK", 200
            if up in ("NO", "NO ACEPTO", "STOP", "BAJA"):
                remove_consent(frm); send("❌ Consentimiento retirado. Ya no procesaré tus audios.\nTus datos se pueden borrar por completo escribiendo BORRAR.\nEscribe \"Hola\" para volver a aceptar."); return "OK", 200
            _today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            _due = get_today_reminders(frm) if reminders_enabled(frm) else []
            if _due: send("⏰ Hoy tienes:\n" + "\n".join(f"- {t}" for t in _due)); mark_reminders_sent(frm, _today)
            if up in ("SIN RECORDATORIOS", "NO RECORDATORIOS", "APAGAR RECORDATORIOS", "SIN AVISOS"):
                set_reminders(frm, False); send("🔕 Recordatorios apagados. Ya no te escribiré yo primero.\nTus tareas siguen guardadas. Para activarlos de nuevo escribe CON RECORDATORIOS."); return "OK", 200
            if up in ("CON RECORDATORIOS", "ACTIVAR RECORDATORIOS", "RECORDATORIOS ON"):
                set_reminders(frm, True); send("🔔 Recordatorios activados. Te avisaré solo de las tareas con fecha que TÚ me pidas."); return "OK", 200
            if up.startswith("BUSCAR ") or up.startswith("BUSCA "):
                q = txt.split(" ", 1)[1].strip() if " " in txt else ""; hits = search_history(frm, q) if q else []
                send("🔎 Resultados para '" + q + "':\n" + ("\n".join(hits) if hits else "No encontré nada con esa palabra.")); return "OK", 200
            if up in ("HOY", "MIS TAREAS", "TAREAS DE HOY", "HOY TENGO"):
                _rem = db_query("SELECT task FROM reminders WHERE user=? AND due_date=?", (frm, _today))
                send("⏰ Hoy tienes:\n" + ("\n".join(f"- {t[0]}" for t in _rem) if _rem else "Hoy no tienes tareas. 🎉")); return "OK", 200
            if up in ("RESUMEN", "RESUMEN DEL DIA", "RESUMEN DEL DÍA", "RESUMEN DE HOY", "PDF", "RESUMO"):
                send("📄 Resumen del día:\n" + get_resumen(frm)); return "OK", 200
            if up in ("FAVORITO", "⭐", "FAVORITOS", "MIS FAVORITOS"):
                if up == "FAVORITO" or up == "⭐":
                    ok = save_favorite(frm); send("⭐ Guardado en favoritos." if ok else "⭐ Aún no tienes transcripciones para guardar.")
                else:
                    favs = get_favorites(frm); send("⭐ Favoritos:\n" + ("\n".join(f"• {str(c)[:10]}: {(t or '').replace(chr(10), ' | ')[:200]}" for c, t in favs) if favs else "Aún no tienes favoritos. Escribe FAVORITO después de una transcripción importante."))
                return "OK", 200
            if up in ("MIS ESTADISTICAS", "MIS ESTADÍSTICAS", "ESTADISTICAS", "ESTADÍSTICAS", "MI CONTADOR", "STATS"):
                row = get_my_stats(frm)
                if not row: send("📊 Aún no tienes transcripciones.\nEnvía un audio y aquí aparecerá tu contador.")
                else: send(f"📊 Tus estadísticas:\n• Transcripciones: {row[0]}\n• Última: {row[1][:16] if row[1] else '-'}\n\n📋 Últimas tareas:\n{row[2] or '• Sin tareas concretas.'}")
                return "OK", 200
            if not has_consent(frm): send_consent(); return "OK", 200
            send("🎙️ VozPronta 10 - ¡Envía un audio de hasta 10 min!\n\n⏰ HOY - tareas de hoy  •  📄 RESUMEN - resumen del día  •  🔎 BUSCAR Juan  •  ⭐ FAVORITO"); return "OK", 200
        if typ in ("audio", "voice"):
            if not has_consent(frm):
                send_consent(); send("⚠️ Primero pulsa \"✅ Sí, acepto\" arriba y vuelve a enviar el audio."); return "OK", 200
            aid = m['audio']['id']
            info = requests.get(f"https://graph.facebook.com/v19.0/{aid}", headers={"Authorization": f"Bearer {TOKEN}"}).json()
            if 'url' not in info: send("❌ Error al descargar el audio"); return "OK", 200
            aud = requests.get(info['url'], headers={"Authorization": f"Bearer {TOKEN}"}).content
            print(f"AUDIO download: {len(aud)} bytes", flush=True)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                tf.write(aud); path = tf.name
            with open(path, 'rb') as f:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}"}, files={"file": f},
                    data={"model": WHISPER_MODEL}, timeout=120)
            os.unlink(path)
            print(f"GROQ whisper: {r.status_code} {r.text[:300]}", flush=True)
            if r.status_code == 200:
                text = r.json().get('text', '')
                # СКОРОСТЬ: сначала счётчик (быстро), потом задачи. Счётчик НЕ зависит от задач.
                try: n = bump_counter(frm, "")
                except Exception as e: print(f"bump_counter failed: {e}", flush=True); n = 0
                try: tasks = extract_tasks(text)
                except Exception as e: print(f"tasks failed: {e}", flush=True); tasks = "- Sin tareas concretas."
                try: save_history(frm, text, tasks)
                except Exception as e: print(f"save_history failed: {e}", flush=True)
                # Обновляем last_tasks у счётчика, чтобы MIS ESTADISTICAS показывали последние задачи
                try: db_execute("UPDATE stats SET last_tasks=? WHERE user=?", (tasks, frm))
                except Exception: pass
                msg = f"✅ Transcripción:\n\n{text}"
                msg += f"\n\n📋 Tareas:\n{tasks or '- Sin tareas concretas.'}"
                # СЧЁТЧИК - ВСЕГДА, даже если n=0 (тогда покажем, что счётчик жив, а не прячем)
                msg += f"\n\n📊 Esta es tu transcripción #{n}." if n else "\n\n📊 Transcripción guardada."
                send(msg[:3900])
            else:
                send(f"❌ Error transcripción (Groq {r.status_code}): {r.text[:300]}")
    except Exception as e:
        print(e, flush=True)
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
