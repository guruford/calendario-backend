"""
Backend - Calendario de Pruebas
Scraper para CMI Escolar + API para la PWA
Basado en análisis del HAR: usa AJAX a calendario_pruebas.php
"""

from flask import Flask, jsonify, make_response
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
def handle_options():
    return make_response("", 204)

CMI_BASE = "https://www.cmiescolar.cl/colegiosarzobispado3"

# Datos del alumno obtenidos del HAR
ALUMNO_ID = "11437"
COLEGIO_ID = "4"
CURSO_ID   = "255"
PERFIL_ID  = "26"

def get_credentials():
    return os.environ.get("CMI_USUARIO", ""), os.environ.get("CMI_CLAVE", "")


def get_cmi_session():
    """Inicia sesión en CMI Escolar."""
    cmi_usuario, cmi_clave = get_credentials()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    # Cargar página de login para obtener tokens
    login_page = session.get(f"{CMI_BASE}/index.php", timeout=15)
    soup = BeautifulSoup(login_page.text, "html.parser")

    payload = {
        "rut":  cmi_usuario,
        "pass": cmi_clave,
    }

    # Capturar campos hidden del formulario
    for hidden in soup.find_all("input", type="hidden"):
        name = hidden.get("name")
        val  = hidden.get("value", "")
        if name:
            payload[name] = val

    # POST login
    resp = session.post(
        f"{CMI_BASE}/proceso_login.php",
        data=payload,
        allow_redirects=True,
        timeout=15
    )

    # Verificar éxito: si nos redirige a ficha.php o inicio.php, login ok
    if "Ingrese sus datos" in resp.text and "ficha.php" not in resp.url:
        return None, "Login fallido — verifica CMI_USUARIO y CMI_CLAVE en Railway"

    return session, None


def get_session_token(session):
    """Obtiene el token de sesión desde la página de inicio."""
    resp = session.get(f"{CMI_BASE}/estudiante/ficha.php", timeout=15, allow_redirects=True)

    # Extraer token de sesión de la URL o del HTML
    # El token aparece como: ?sesion=XXXX en los links
    token_match = re.search(r'sesion=([A-Za-z0-9+/=]+)', resp.url)
    if not token_match:
        token_match = re.search(r'sesion=([A-Za-z0-9+/=]+)', resp.text)

    if token_match:
        return token_match.group(1)
    return None


def fetch_calendar_month(session, mes, annio="2026"):
    """Llama al endpoint AJAX del calendario para un mes específico."""
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
    """Extrae eventos del HTML devuelto por calendario_pruebas.php."""
    soup = BeautifulSoup(html, "html.parser")
    eventos = []

    # Buscar en tablas
    for tabla in soup.find_all("table"):
        filas = tabla.find_all("tr")
        for fila in filas:
            celdas = [td.get_text(strip=True) for td in fila.find_all(["td", "th"])]
            if len(celdas) < 2:
                continue

            texto_fila = " ".join(celdas)

            # Buscar fecha en formato dd/mm, dd-mm, o número de día
            fecha = None

            # Formato completo dd/mm/yyyy o dd-mm-yyyy
            match = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', texto_fila)
            if match:
                d, m, y = match.groups()
                y = int(y)
                if y < 100: y += 2000
                fecha = f"{y}-{int(m):02d}-{int(d):02d}"
            else:
                # Solo número de día — usar mes y año actuales
                match_day = re.search(r'^\s*(\d{1,2})\s*$', celdas[0])
                if match_day:
                    d = int(match_day.group(1))
                    if 1 <= d <= 31:
                        fecha = f"{annio}-{int(mes):02d}-{d:02d}"

            if fecha:
                # El título suele estar en la segunda celda
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

    # Si no encontramos en tablas, buscar elementos con clase de evento
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
    if "tarea" in texto:   return "Tarea"
    if "disert" in texto:  return "Disertación"
    if "prueba" in texto or "eval" in texto: return "Prueba"
    return "Evaluación"


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.route("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "Calendario de Pruebas — Backend CMI Escolar",
        "version": "2.0.0"
    })


@app.route("/api/status")
def status():
    cmi_rut, cmi_pass = get_credentials()
    return jsonify({
        "cmi_usuario_configured":  bool(cmi_usuario),
        "cmi_clave_configured": bool(cmi_clave),
        "alumno_id": ALUMNO_ID,
        "ready": bool(cmi_usuario and cmi_clave)
    })


@app.route("/api/calendario")
def get_calendario():
    cmi_usuario, cmi_clave = get_credentials()

    if not cmi_rut or not cmi_pass:
        return jsonify({
            "error": "Credenciales no configuradas. Agrega CMI_usuario y CMI_clave en Railway → Variables."
        }), 500

    session, err = get_cmi_session()
    if err:
        return jsonify({"error": err}), 401

    # Obtener calendario para los próximos meses
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
        "errores": errores if errores else None
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
