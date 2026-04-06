"""
Backend - Calendario de Pruebas
Scraper para CMI Escolar + API para la PWA
"""

from flask import Flask, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import os
import re
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Permite requests desde la PWA

CMI_BASE = "https://www.cmiescolar.cl/colegiosarzobispado3"
CMI_RUT  = os.environ.get("CMI_RUT", "")        # Variable de entorno en Railway
CMI_PASS = os.environ.get("CMI_PASS", "")       # Variable de entorno en Railway


def get_cmi_session():
    """Inicia sesión en CMI Escolar y retorna la sesión autenticada."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    # Primero cargamos la página de login para obtener tokens si los hay
    login_page = session.get(f"{CMI_BASE}/index.php")
    soup = BeautifulSoup(login_page.text, "html.parser")

    # Datos de login
    payload = {
        "rut":  CMI_RUT,
        "pass": CMI_PASS,
    }

    # Buscamos si hay campos hidden adicionales (tokens CSRF, etc.)
    for hidden in soup.find_all("input", type="hidden"):
        name = hidden.get("name")
        val  = hidden.get("value", "")
        if name:
            payload[name] = val

    # Enviamos el login
    resp = session.post(
        f"{CMI_BASE}/proceso_login.php",
        data=payload,
        allow_redirects=True
    )

    # Verificamos que el login fue exitoso (no volvimos al login)
    if "Ingrese sus datos" in resp.text or "index.php" in resp.url:
        return None, "Login fallido — revisa RUT y contraseña en las variables de entorno"

    return session, None


def scrape_calendario(session):
    """Extrae los eventos del calendario desde CMI Escolar."""
    # Intentamos diferentes rutas comunes para el calendario
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
        resp = session.get(url)
        if resp.status_code == 200 and len(resp.text) > 500:
            # Verifica que no nos redirigió al login
            if "Ingrese sus datos" not in resp.text:
                page_html = resp.text
                break

    if not page_html:
        return [], "No se encontró la página del calendario. Puede que la URL haya cambiado."

    soup = BeautifulSoup(page_html, "html.parser")

    # Buscamos tablas de evaluaciones/calendario
    tablas = soup.find_all("table")
    for tabla in tablas:
        filas = tabla.find_all("tr")
        for fila in filas[1:]:  # skip header
            celdas = [td.get_text(strip=True) for td in fila.find_all(["td", "th"])]
            if len(celdas) >= 2:
                # Intentamos detectar fechas en cualquier celda
                fecha = None
                for celda in celdas:
                    fecha_match = re.search(
                        r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", celda
                    )
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
                    evento = {
                        "id":      f"cmi_{hash(''.join(celdas))}",
                        "date":    fecha,
                        "title":   celdas[1] if len(celdas) > 1 else celdas[0],
                        "subject": celdas[0] if len(celdas) > 0 else "",
                        "type":    detectar_tipo(celdas),
                        "source":  "cmi",
                        "raw":     celdas,
                    }
                    eventos.append(evento)

    # Si no encontramos tabla, buscamos elementos con clase "evento", "evaluacion", etc.
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
                    "id":      f"cmi_{hash(texto)}",
                    "date":    f"{y}-{int(m):02d}-{int(d):02d}",
                    "title":   texto[:100],
                    "subject": "",
                    "type":    detectar_tipo([texto]),
                    "source":  "cmi",
                })

    return eventos, None


def detectar_tipo(celdas):
    """Detecta el tipo de evaluación a partir del texto."""
    texto = " ".join(celdas).lower()
    if "solemne" in texto:
        return "Solemne"
    if "control" in texto:
        return "Control"
    if "tarea" in texto:
        return "Tarea"
    if "disert" in texto:
        return "Disertación"
    if "prueba" in texto or "eval" in texto:
        return "Prueba"
    return "Evaluación"


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.route("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "Calendario de Pruebas — Backend CMI Escolar",
        "version": "1.0.0"
    })


@app.route("/api/calendario")
def get_calendario():
    """
    Endpoint principal: inicia sesión en CMI y retorna las evaluaciones.
    La PWA llama a este endpoint cada vez que se abre.
    """
    if not CMI_RUT or not CMI_PASS:
        return jsonify({
            "error": "Credenciales no configuradas. Agrega CMI_RUT y CMI_PASS en Railway."
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


@app.route("/api/status")
def status():
    """Verifica si las credenciales están configuradas (sin exponerlas)."""
    return jsonify({
        "cmi_rut_configured":  bool(CMI_RUT),
        "cmi_pass_configured": bool(CMI_PASS),
        "ready": bool(CMI_RUT and CMI_PASS)
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
