"""
Backend - Calendario de Pruebas v2.1.0
Scraper CMI Escolar — campos de login: usuario + clave
"""

from flask import Flask, jsonify, make_response, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import os
import re
from datetime import datetime

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.route("/", methods=["OPTIONS"])
@app.route("/api/status", methods=["OPTIONS"])
@app.route("/api/calendario", methods=["OPTIONS"])
@app.route("/api/debug-login", methods=["OPTIONS"])
def handle_options():
    return make_response("", 204)

CMI_BASE = "https://www.cmiescolar.cl/colegiosarzobispado3"

# Datos del alumno (obtenidos del HAR)
ALUMNO_ID = "11437"
COLEGIO_ID = "4"
CURSO_ID   = "255"
PERFIL_ID  = "26"

def get_credentials():
    return os.environ.get("CMI_RUT", ""), os.environ.get("CMI_PASS", "")


def get_cmi_session():
    """Inicia sesión en CMI Escolar."""
    cmi_rut, cmi_pass = get_credentials()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.5",
    })

    # Cargar login page para obtener todos los campos hidden
    login_page = session.get(f"{CMI_BASE}/index.php", timeout=15)
    soup = BeautifulSoup(login_page.text, "html.parser")

    # Extraer TODOS los inputs del formulario
    payload = {}
    for inp in soup.find_all("input"):
        name = inp.get("name")
        val  = inp.get("value", "")
        if name:
            payload[name] = val

    # Campos confirmados por debug-login:
    #   name="usuario" → RUT
    #   name="pass"    → contraseña
    #   action="inicio.php"
    payload["usuario"] = cmi_rut
    payload["pass"]    = cmi_pass

    # POST al endpoint correcto: inicio.php
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

    # Si seguimos en la página de login, falló
    if "Ingrese sus datos" in resp.text or (
        "index.php" in resp.url and "sesion=" not in resp.url
        and "inicio.php" not in resp.url
    ):
        return None, f"Login fallido (status {resp.status_code}). URL final: {resp.url}"

    return session, None


def fetch_calendar_month(session, mes, annio="2026"):
    """Llama al endpoint AJAX del calendario."""
    url = f"{CMI_BASE}/incluidos/calendario_pruebas.php"
    payload = {
        "alumno":  ALUMNO_ID,
        "colegio": COLEGIO_ID,
        "annio":   annio,
        "curso":   CURSO_ID,
        "mes":     str(mes),
        "perfil":  PERFIL_ID,
    }
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"{CMI_BASE}/estudiante/ficha.php",
    }
    resp = session.post(url, data=payload, headers=headers, timeout=15)
    if resp.status_code != 200:
        return []
    return parse_calendar_html(resp.text, mes, annio)


MESES_ES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
}

def parse_fecha_texto(texto, annio):
    """Convierte '15 de Abril' → '2026-04-15'"""
    match = re.search(r'(\d{1,2})\s+de\s+(\w+)', texto, re.IGNORECASE)
    if match:
        dia = int(match.group(1))
        mes_str = match.group(2).lower()
        mes = MESES_ES.get(mes_str)
        if mes:
            return f"{annio}-{mes:02d}-{dia:02d}"
    return None


def parse_calendar_html(html, mes, annio):
    """
    Parser del calendario de CMI Escolar.
    El HTML tiene tooltips con estructura:
      - "Ev. Sumativa: ASIGNATURA"
      - "Título: Prueba"
      - "Asignatura: NOMBRE"
      - "Fecha: 15 de Abril"
      - Botón Cerrar
    Cada tooltip está dentro de una celda <td> del calendario.
    """
    soup = BeautifulSoup(html, "html.parser")
    eventos = []
    ids_vistos = set()

    # Buscar todas las celdas que contienen una evaluación
    # El indicador clave es que tengan el texto "Fecha:" dentro
    for celda in soup.find_all(["td", "div"]):
        texto_celda = celda.get_text(separator="\n", strip=True)

        # Solo procesar celdas que tengan "Fecha:" — son las que tienen prueba
        if "Fecha:" not in texto_celda and "Fecha" not in texto_celda:
            continue

        # Extraer fecha del formato "Fecha: 15 de Abril" o "Fecha15 de Abril"
        fecha_match = re.search(
            r'Fecha[:\s]*(\d{1,2}\s+de\s+\w+)',
            texto_celda,
            re.IGNORECASE
        )
        if not fecha_match:
            continue

        fecha_str = fecha_match.group(1).strip()
        fecha = parse_fecha_texto(fecha_str, annio)
        if not fecha:
            continue

        # Extraer Asignatura
        asig_match = re.search(r'Asignatura[:\s]*([^\n]+)', texto_celda)
        asignatura = asig_match.group(1).strip() if asig_match else ""

        # Extraer Título de la evaluación
        titulo_match = re.search(r'T[íi]tulo[:\s]*([^\n]+)', texto_celda)
        titulo = titulo_match.group(1).strip() if titulo_match else asignatura

        # Limpiar strings que puedan tener residuos HTML
        titulo     = re.sub(r'\s+', ' ', titulo).strip()
        asignatura = re.sub(r'\s+', ' ', asignatura).strip()

        # Evitar duplicados (mismo día + misma asignatura)
        clave = f"{fecha}_{asignatura}_{titulo}"
        if clave in ids_vistos:
            continue
        ids_vistos.add(clave)

        # Extraer tipo de evaluación
        tipo_match = re.search(r'Tipo de evaluaci[oó]n[:\s]*([^\n]+)', texto_celda)
        tipo_raw = tipo_match.group(1).strip() if tipo_match else ""

        eventos.append({
            "id":      f"cmi_{abs(hash(clave))}",
            "date":    fecha,
            "title":   titulo,
            "subject": asignatura,
            "type":    detectar_tipo([titulo, tipo_raw, asignatura]),
            "source":  "cmi",
        })

    return eventos


def detectar_tipo(celdas):
    texto = " ".join(celdas).lower()
    if "solemne" in texto: return "Solemne"
    if "control" in texto: return "Control"
    if "tarea"   in texto: return "Tarea"
    if "disert"  in texto: return "Disertación"
    if "prueba" in texto or "eval" in texto: return "Prueba"
    return "Evaluación"


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.route("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "Calendario de Pruebas — Backend CMI Escolar",
        "version": "2.3.0"
    })


@app.route("/api/status")
def status():
    cmi_rut, cmi_pass = get_credentials()
    return jsonify({
        "cmi_rut_configured":  bool(cmi_rut),
        "cmi_pass_configured": bool(cmi_pass),
        "ready": bool(cmi_rut and cmi_pass)
    })


@app.route("/api/debug-login")
def debug_login():
    """Endpoint de diagnóstico — muestra qué campos tiene el formulario de login."""
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        resp = session.get(f"{CMI_BASE}/index.php", timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        campos = []
        for inp in soup.find_all("input"):
            campos.append({
                "name":  inp.get("name"),
                "type":  inp.get("type"),
                "id":    inp.get("id"),
                "placeholder": inp.get("placeholder"),
            })

        forms = []
        for form in soup.find_all("form"):
            forms.append({
                "action": form.get("action"),
                "method": form.get("method"),
            })

        return jsonify({
            "campos_encontrados": campos,
            "formularios": forms,
            "url_final": resp.url,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendario")
def get_calendario():
    cmi_rut, cmi_pass = get_credentials()
    if not cmi_rut or not cmi_pass:
        return jsonify({
            "error": "Credenciales no configuradas. Agrega CMI_RUT y CMI_PASS en Railway → Variables."
        }), 500

    session, err = get_cmi_session()
    if err:
        return jsonify({"error": err}), 401

    annio = str(datetime.now().year)
    mes_actual = datetime.now().month
    todos_eventos = []
    errores = []

    for mes in range(mes_actual, 13):
        try:
            eventos = fetch_calendar_month(session, mes, annio)
            todos_eventos.extend(eventos)
        except Exception as e:
            errores.append(f"Mes {mes}: {str(e)}")

    return jsonify({
        "source":  "cmi",
        "fetched": datetime.now().isoformat(),
        "count":   len(todos_eventos),
        "eventos": todos_eventos,
        "errores": errores if errores else None,
    })



@app.route("/api/debug-html")
def debug_html():
    """Muestra el HTML crudo del calendario para un mes."""
    cmi_rut, cmi_pass = get_credentials()
    if not cmi_rut or not cmi_pass:
        return jsonify({"error": "Sin credenciales"}), 500
    session, err = get_cmi_session()
    if err:
        return jsonify({"error": err}), 401
    url = f"{CMI_BASE}/incluidos/calendario_pruebas.php"
    payload = {
        "alumno": ALUMNO_ID, "colegio": COLEGIO_ID,
        "annio": "2026", "curso": CURSO_ID,
        "mes": "4", "perfil": PERFIL_ID,
    }
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{CMI_BASE}/estudiante/ficha.php"}
    resp = session.post(url, data=payload, headers=headers, timeout=15)
    return resp.text, 200, {"Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
# TEMPORAL: endpoint para ver HTML crudo
