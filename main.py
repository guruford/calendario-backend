"""
Backend - Calendario de Pruebas v2.4.0
Scraper CMI Escolar + almacenamiento de eventos manuales (SQLite)
"""

from flask import Flask, jsonify, make_response, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import os
import re
import sqlite3
import uuid
from datetime import datetime

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.route("/", methods=["OPTIONS"])
@app.route("/api/status", methods=["OPTIONS"])
@app.route("/api/calendario", methods=["OPTIONS"])
@app.route("/api/eventos-manuales", methods=["OPTIONS"])
@app.route("/api/eventos-manuales/<eid>", methods=["OPTIONS"])
def handle_options(**kwargs):
    return make_response("", 204)

CMI_BASE  = "https://www.cmiescolar.cl/colegiosarzobispado3"
ALUMNO_ID = "11437"
COLEGIO_ID = "4"
CURSO_ID   = "255"
PERFIL_ID  = "26"

# ─── SQLite para eventos manuales ─────────────────────────────────────────────
DB_PATH = os.environ.get("DB_PATH", "/data/eventos.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eventos_manuales (
            id      TEXT PRIMARY KEY,
            subject TEXT,
            date    TEXT,
            type    TEXT,
            desc    TEXT,
            source  TEXT DEFAULT 'manual',
            created TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

try:
    init_db()
except Exception as e:
    print(f"Warning: DB init failed: {e}")

# ─── Credenciales ─────────────────────────────────────────────────────────────
def get_credentials():
    return os.environ.get("CMI_RUT", ""), os.environ.get("CMI_PASS", "")

# ─── Login CMI ────────────────────────────────────────────────────────────────
def get_cmi_session():
    cmi_rut, cmi_pass = get_credentials()
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.5",
    })
    login_page = session.get(f"{CMI_BASE}/index.php", timeout=15)
    soup = BeautifulSoup(login_page.text, "html.parser")
    payload = {}
    for inp in soup.find_all("input"):
        name = inp.get("name")
        val  = inp.get("value", "")
        if name:
            payload[name] = val
    # Campos confirmados por debug-login
    payload["usuario"] = cmi_rut
    payload["pass"]    = cmi_pass
    resp = session.post(
        f"{CMI_BASE}/inicio.php",
        data=payload,
        allow_redirects=True,
        timeout=15,
        headers={
            "Referer": f"{CMI_BASE}/index.php",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.cmiescolar.cl",
        }
    )
    if "Ingrese sus datos" in resp.text and "sesion=" not in resp.url:
        return None, f"Login fallido. URL final: {resp.url}"
    return session, None

# ─── Parser del calendario ────────────────────────────────────────────────────
MESES_ES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
}

def parse_fecha_texto(texto, annio):
    match = re.search(r'(\d{1,2})\s+de\s+(\w+)', texto, re.IGNORECASE)
    if match:
        dia = int(match.group(1))
        mes = MESES_ES.get(match.group(2).lower())
        if mes:
            return f"{annio}-{mes:02d}-{dia:02d}"
    return None

def detectar_tipo(celdas):
    texto = " ".join(celdas).lower()
    if "solemne"  in texto: return "Solemne"
    if "control"  in texto: return "Control"
    if "tarea"    in texto: return "Tarea"
    if "disert"   in texto: return "Disertación"
    if "prueba"   in texto or "eval" in texto: return "Prueba"
    return "Evaluación"

def parse_calendar_html(html, mes, annio):
    soup = BeautifulSoup(html, "html.parser")
    eventos = []
    ids_vistos = set()
    for celda in soup.find_all(["td", "div"]):
        texto_celda = celda.get_text(separator="\n", strip=True)
        if "Fecha:" not in texto_celda and "Fecha" not in texto_celda:
            continue
        fecha_match = re.search(r'Fecha[:\s]*(\d{1,2}\s+de\s+\w+)', texto_celda, re.IGNORECASE)
        if not fecha_match:
            continue
        fecha = parse_fecha_texto(fecha_match.group(1).strip(), annio)
        if not fecha:
            continue
        asig_match   = re.search(r'Asignatura[:\s]*([^\n]+)', texto_celda)
        titulo_match = re.search(r'T[íi]tulo[:\s]*([^\n]+)', texto_celda)
        tipo_match   = re.search(r'Tipo de evaluaci[oó]n[:\s]*([^\n]+)', texto_celda)
        asignatura = re.sub(r'\s+', ' ', asig_match.group(1).strip())   if asig_match   else ""
        titulo     = re.sub(r'\s+', ' ', titulo_match.group(1).strip()) if titulo_match else asignatura
        tipo_raw   = re.sub(r'\s+', ' ', tipo_match.group(1).strip())   if tipo_match   else ""
        clave = f"{fecha}_{asignatura}_{titulo}"
        if clave in ids_vistos:
            continue
        ids_vistos.add(clave)
        eventos.append({
            "id":      f"cmi_{abs(hash(clave))}",
            "date":    fecha,
            "title":   titulo,
            "subject": asignatura,
            "type":    detectar_tipo([titulo, tipo_raw, asignatura]),
            "source":  "cmi",
        })
    return eventos

def fetch_calendar_month(session, mes, annio="2026"):
    url     = f"{CMI_BASE}/incluidos/calendario_pruebas.php"
    payload = {"alumno": ALUMNO_ID, "colegio": COLEGIO_ID,
               "annio": annio, "curso": CURSO_ID, "mes": str(mes), "perfil": PERFIL_ID}
    headers = {"X-Requested-With": "XMLHttpRequest",
               "Content-Type": "application/x-www-form-urlencoded",
               "Referer": f"{CMI_BASE}/estudiante/ficha.php"}
    resp = session.post(url, data=payload, headers=headers, timeout=15)
    return parse_calendar_html(resp.text, mes, annio) if resp.status_code == 200 else []

# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.route("/")
def health():
    return jsonify({"status": "ok",
                    "service": "Calendario de Pruebas — Backend CMI Escolar",
                    "version": "2.4.0"})

@app.route("/api/status")
def status():
    cmi_rut, cmi_pass = get_credentials()
    return jsonify({"cmi_rut_configured": bool(cmi_rut),
                    "cmi_pass_configured": bool(cmi_pass),
                    "ready": bool(cmi_rut and cmi_pass)})

@app.route("/api/calendario")
def get_calendario():
    cmi_rut, cmi_pass = get_credentials()
    if not cmi_rut or not cmi_pass:
        return jsonify({"error": "Credenciales no configuradas en Railway."}), 500
    session, err = get_cmi_session()
    if err:
        return jsonify({"error": err}), 401
    annio      = str(datetime.now().year)
    mes_actual = datetime.now().month
    todos_eventos = []
    errores = []
    for mes in range(mes_actual, 13):
        try:
            todos_eventos.extend(fetch_calendar_month(session, mes, annio))
        except Exception as e:
            errores.append(f"Mes {mes}: {str(e)}")
    return jsonify({"source": "cmi", "fetched": datetime.now().isoformat(),
                    "count": len(todos_eventos), "eventos": todos_eventos,
                    "errores": errores or None})

# ─── Eventos manuales (sincronizados entre dispositivos) ──────────────────────

@app.route("/api/eventos-manuales", methods=["GET"])
def get_eventos_manuales():
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM eventos_manuales ORDER BY date").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/eventos-manuales", methods=["POST"])
def create_evento_manual():
    try:
        data = request.get_json()
        if not data or not data.get("date") or not data.get("subject"):
            return jsonify({"error": "Se requiere date y subject"}), 400
        eid = data.get("id") or f"manual_{uuid.uuid4().hex[:12]}"
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO eventos_manuales (id,subject,date,type,desc,source,created) VALUES (?,?,?,?,?,?,?)",
            (eid, data.get("subject",""), data.get("date",""),
             data.get("type","Prueba"), data.get("desc",""),
             data.get("source","manual"), datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "id": eid}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/eventos-manuales/<eid>", methods=["DELETE"])
def delete_evento_manual(eid):
    try:
        conn = get_db()
        conn.execute("DELETE FROM eventos_manuales WHERE id=?", (eid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/debug-login")
def debug_login():
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        resp = session.get(f"{CMI_BASE}/index.php", timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        campos = [{"name": inp.get("name"), "type": inp.get("type"),
                   "id": inp.get("id"), "placeholder": inp.get("placeholder")}
                  for inp in soup.find_all("input")]
        forms  = [{"action": f.get("action"), "method": f.get("method")}
                  for f in soup.find_all("form")]
        return jsonify({"campos_encontrados": campos, "formularios": forms})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
