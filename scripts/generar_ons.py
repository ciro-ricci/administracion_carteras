"""
Genera /data/ons.json con el rendimiento (TIR desde USD) de cada Obligacion
Negociable (tipo "ON" en tenencias.json), tomado de la planilla publica de
Google Sheets que usa el usuario para seguimiento de ONs:

  https://docs.google.com/spreadsheets/d/1y8OXueedijhun4d-v2yz8KcimRR58mQSmzs0t1CKUjg
  (gid=387376027)

La planilla trae, entre otras columnas, "TickerARS" (el codigo de la especie
tal como opera en pesos en el mercado local, ej. "CS47O") y "TIR desde USD"
(columna K), que es el rendimiento que pidio el usuario.

Reglas de negocio:
  - Solo se buscan los tickers que aparecen con tipo "ON" en
    data/tenencias.json (no se procesan Bonos, Cedears, etc. de la planilla).
  - El cruce entre tenencias.json y la planilla se hace por el codigo exacto
    de la columna "TickerARS".
  - Si un ticker de tenencias.json no aparece en la planilla, o su celda de
    TIR es "#N/A" o esta vacia, NO se inventa un valor: se marca
    sin_dato=true con el motivo correspondiente.

Salida: data/ons.json
"""

import csv
import io
import json
import urllib.request
from datetime import datetime, timezone

TENENCIAS_PATH = "data/tenencias.json"
OUTPUT_PATH = "data/ons.json"

SHEET_ID = "1y8OXueedijhun4d-v2yz8KcimRR58mQSmzs0t1CKUjg"
GID = "387376027"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"


def get_tickers_on():
    with open(TENENCIAS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    tickers = set()
    for c in data["comitentes"]:
        for t in c["tenencias"]:
            if t["tipo"] == "ON":
                tickers.add(t["ticker"])
    return tickers


def descargar_csv(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        contenido = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(contenido)))


def parse_pct(valor: str):
    """Convierte '5,80%' -> 5.80. '#N/A' o vacio -> None."""
    if not valor:
        return None
    valor = valor.strip()
    if not valor or valor == "#N/A":
        return None
    try:
        return float(valor.replace("%", "").replace(".", "").replace(",", "."))
    except ValueError:
        return None


def generar():
    tickers_on = get_tickers_on()
    filas = descargar_csv(CSV_URL)

    por_ticker_ars = {}
    for fila in filas:
        ticker_ars = (fila.get("TickerARS") or "").strip()
        if ticker_ars:
            por_ticker_ars[ticker_ars] = fila

    salida = {"tickers": {}}
    for ticker in sorted(tickers_on):
        fila = por_ticker_ars.get(ticker)
        if fila is None:
            salida["tickers"][ticker] = {
                "sin_dato": True,
                "motivo": "El ticker no aparece en la planilla de ONs.",
            }
            continue
        tir_usd = parse_pct(fila.get("TIR desde USD", ""))
        if tir_usd is None:
            salida["tickers"][ticker] = {
                "sin_dato": True,
                "motivo": "TIR desde USD sin dato (#N/A o vacio) en la planilla.",
            }
        else:
            salida["tickers"][ticker] = {
                "tir_usd_pct": tir_usd,
                "empresa": (fila.get("Empresa") or "").strip() or None,
                "vencimiento": (fila.get("Vto.") or "").strip() or None,
            }

    salida["generado"] = datetime.now(timezone.utc).isoformat()
    salida["fuente"] = "Google Sheet publica de seguimiento de ONs (columna 'TIR desde USD')."

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    ok = sum(1 for v in salida["tickers"].values() if not v.get("sin_dato"))
    sin_dato = len(salida["tickers"]) - ok
    print(f"OK: {ok} con TIR, {sin_dato} sin dato. Total ONs en tenencias: {len(salida['tickers'])}")
    for ticker, v in salida["tickers"].items():
        if v.get("sin_dato"):
            print(f"  sin dato: {ticker} -> {v.get('motivo')}")


if __name__ == "__main__":
    generar()
