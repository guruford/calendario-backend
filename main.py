"""
Backend - Calendario de Pruebas
Scraper para CMI Escolar + API para la PWA
"""

from flask import Flask, jsonify, make_response
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import os
import re
from datetime import datetime

app = Flask(__name__)

# CORS explícito — permite cualquier origen (necesario para GitHub Pages)
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

def get_credentials():
    """Lee las credenciales en cada request para que Railway las detecte siempre."""
    return os.environ.get("CMI_RUT", ""), os.environ.get("CMI_PASS", "")


def get_cmi_session():
    """Inicia sesión en CMI Escolar y retorna la sesión autenticada."""
    cmi_rut, cmi_pass = get_credentials()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    login_page = session.get(f"{CMI_BASE}/index.php")
    soup = BeautifulSoup(login_page.text, "html.parser")

    payload = {
        "rut":  cmi_rut,
        "pass": cmi_pass,
    }

    for hidden in soup.find_all("input", type="hidden"):
        name = hidden.get("name")
        val  = hidden.get("value", "")
        if name:
            payload[name] = val

    resp = session.post(
        f"{CMI_BASE}/proceso_login.php",
        data=payload,
        allow_redirects=True
    )

    if "Ingrese sus datos" in resp.text or resp.url.endswith("index.php"):
        return None, "Login fallido — revisa CMI_RUT y CMI_PASS en Railway"

    return session, None


def scrape_calendario(session):
    """Extrae los eventos del calendario desde CMI Escolar."""
    calendar_urls = [
        f"{CMI_BASE}/calendario.php",
        f"{CMI_BASE}/agenda.php",
        f"{CMI_BASE}/evaluaciones.php",
        f"{CMI_BASE}/alumno/calendario.php",
        f"{CMI_BASE}/alumno/agenda.php",
        f"{CMI_BASE}/alumno/evaluaciones.php",
    ]

    eventos = []
    page_html = None

    for url in calendar_urls:
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200 and len(resp.text) > 500:
                if "Ingrese sus datos" not in resp.text:
                    page_html = resp.text
                    break
        except:
            continue

    if not page_html:
        return [], "No se encontró la página del calendario."

    soup = BeautifulSoup(page_html, "html.parser")

    for tabla in soup.find_all("table"):
        filas = tabla.find_all("tr")
        for fila in filas[1:]:
            celdas = [td.get_text(strip=True) for td in fila.find_all(["td", "th"])]
            if len(celdas) >= 2:
                fecha = None
                for celda in celdas:
                    fecha_match = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", celda)
                    if fecha_match:
                        try:
                            d, m, y = fecha_match.groups()
                            y = int(y)
                            if y < 100:
                                y += 2000
                            fecha = f"{y}-{int(m):02d}-{int(d):02d}"
                        except:
                            pass
                        break

                if fecha:
                    eventos.append({
                        "id":      f"cmi_{abs(hash(''.join(celdas)))}",
                        "date":    fecha,
                        "title":   celdas[1] if len(celdas) > 1 else celdas[0],
                        "subject": celdas[0] if len(celdas) > 0 else "",
                        "type":    detectar_tipo(celdas),
                        "source":  "cmi",
                    })

    if not eventos:
        for el in soup.find_all(class_=re.compile(r"event|eval|prueba|calend|agenda", re.I)):
            texto = el.get_text(separator=" ", strip=True)
            fecha_match = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", texto)
            if fecha_match:
                d, m, y = fecha_match.groups()
                y = int(y)
                if y < 100:
                    y += 2000
                eventos.append({
                    "id":      f"cmi_{abs(hash(texto))}",
                    "date":    f"{y}-{int(m):02d}-{int(d):02d}",
                    "title":   texto[:100],
                    "subject": "",
                    "type":    detectar_tipo([texto]),
                    "source":  "cmi",
                })

    return eventos, None


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
        "version": "1.1.0"
    })


@app.route("/api/status")
def status():
    cmi_rut, cmi_pass = get_credentials()
    return jsonify({
        "cmi_rut_configured":  bool(cmi_rut),
        "cmi_pass_configured": bool(cmi_pass),
        "ready": bool(cmi_rut and cmi_pass)
    })


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

    eventos, err = scrape_calendario(session)
    if err:
        return jsonify({"error": err}), 500

    return jsonify({
        "source":  "cmi",
        "fetched": datetime.now().isoformat(),
        "count":   len(eventos),
        "eventos": eventos
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
