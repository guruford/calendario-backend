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

    # Los campos de la pantalla dicen "Usuario" y "Clave"
    # Probamos todos los nombres posibles para asegurar
    payload["usuario"]  = cmi_rut
    payload["clave"]    = cmi_pass
    # Por si acaso también:
    payload["rut"]      = cmi_rut
    payload["pass"]     = cmi_pass
    payload["password"] = cmi_pass
    payload["user"]     = cmi_rut

    # POST al endpoint de login
    resp = session.post(
        f"{CMI_BASE}/proceso_login.php",
        data=payload,
        allow_redirects=True,
        timeout=15,
        headers={
            "Referer": f"{CMI_BASE}/index.php",
            "Content-Type": "application/x-www-form-urlencoded",
        }
    )

    # Si seguimos en la página de login, falló
    if "Ingrese sus datos" in resp.text or (
        "index.php" in resp.url and "sesion=" not in resp.url
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


def parse_calendar_html(html, mes, annio):
    soup = BeautifulSoup(html, "html.parser")
    eventos = []

    for tabla in soup.find_all("table"):
        for fila in tabla.find_all("tr"):
            celdas = [td.get_text(strip=True) for td in fila.find_all(["td", "th"])]
            if len(celdas) < 2:
                continue
            texto_fila = " ".join(celdas)
            fecha = None
            match = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', texto_fila)
            if match:
                d, m, y = match.groups()
                y = int(y)
                if y < 100: y += 2000
                fecha = f"{y}-{int(m):02d}-{int(d):02d}"
            else:
                match_day = re.search(r'^\s*(\d{1,2})\s*$', celdas[0])
                if match_day:
                    d = int(match_day.group(1))
                    if 1 <= d <= 31:
                        fecha = f"{annio}-{int(mes):02d}-{d:02d}"
            if fecha:
                titulo = celdas[1] if len(celdas) > 1 else celdas[0]
                asignatura = celdas[2] if len(celdas) > 2 else ""
                if titulo and len(titulo) > 1:
                    eventos.append({
                        "id":      f"cmi_{abs(hash(fecha + titulo))}",
                        "date":    fecha,
                        "title":   titulo,
                        "subject": asignatura or titulo,
                        "type":    detectar_tipo([titulo, asignatura]),
                        "source":  "cmi",
                    })

    if not eventos:
        for el in soup.find_all(class_=re.compile(r"event|eval|prueba|calend|nota|item", re.I)):
            texto = el.get_text(separator=" ", strip=True)
            if len(texto) < 3:
                continue
            match = re.search(r'(\d{1,2})[/\-](\d{1,2})', texto)
            if match:
                d, m = match.groups()
                fecha = f"{annio}-{int(m):02d}-{int(d):02d}"
                eventos.append({
                    "id":      f"cmi_{abs(hash(texto))}",
                    "date":    fecha,
                    "title":   texto[:100],
                    "subject": "",
                    "type":    detectar_tipo([texto]),
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
        "version": "2.1.0"
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
