"""
Genera /data/tenencias.json a partir de la planilla publica de Google Sheets
con las tenencias por comitente.

Fuente:
  https://docs.google.com/spreadsheets/d/1lBrdARUWpD8ar_WyWFTlE9R5hbXwwArhyj4dC_qXcJU
  (hoja "tenencias", gid=1893040694)

Reglas de negocio:
  - Se excluyen por completo los tipos de instrumento "Pagare", "Cheque
    Electronico" y "FCIDD" (no son posiciones de mercado, son efectivo /
    colocaciones de corto plazo). No aparecen ni en el detalle ni en el
    total de la cartera.
  - El total de cada comitente y el % de participacion de cada ticker se
    calculan solo sobre lo que queda: Acciones, Cedear, ON, Bonos, Letras,
    Futuros, Opcion de Futuro.
  - Nunca se inventan ni se completan datos faltantes: si un valor no esta
    en la planilla, se omite.

Salida: data/tenencias.json
"""

import csv
import io
import json
import urllib.request
from datetime import datetime, timezone

SHEET_ID = "1lBrdARUWpD8ar_WyWFTlE9R5hbXwwArhyj4dC_qXcJU"
GID = "1893040694"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"

TIPOS_EXCLUIDOS = {"Pagaré", "Cheque Electrónico", "FCIDD"}

OUTPUT_PATH = "data/tenencias.json"


def parse_monto(valor: str) -> float:
    """Convierte '1.234.567,89' (formato ARG) a float. Cadena vacia -> 0.0"""
    if not valor:
        return 0.0
    return float(valor.replace(".", "").replace(",", "."))


def descargar_csv(url: str) -> list[dict]:
    with urllib.request.urlopen(url) as resp:
        contenido = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(contenido)))


def generar():
    filas = descargar_csv(CSV_URL)

    comitentes = {}  # comitente -> {"nombre": ..., "tenencias": {ticker: {"tipo":..., "importe": float}}}

    for fila in filas:
        tipo = fila["Tipo"].strip()
        if tipo in TIPOS_EXCLUIDOS:
            continue

        comitente = fila["Comitente"].strip()
        nombre = fila["Nombre"].strip()
        ticker = fila["Ticker"].strip()
        importe = parse_monto(fila["Valorizado"])

        c = comitentes.setdefault(comitente, {"nombre": nombre, "tickers": {}})
        item = c["tickers"].setdefault(ticker, {"tipo": tipo, "importe": 0.0})
        item["importe"] += importe

    salida = {"generado": datetime.now(timezone.utc).isoformat(), "comitentes": []}

    for comitente, datos in sorted(comitentes.items()):
        total = sum(t["importe"] for t in datos["tickers"].values())
        if total <= 0:
            continue

        tenencias = [
            {
                "ticker": ticker,
                "tipo": t["tipo"],
                "importe": round(t["importe"], 2),
                "participacion_pct": round(t["importe"] / total * 100, 2),
            }
            for ticker, t in datos["tickers"].items()
        ]
        tenencias.sort(key=lambda x: x["importe"], reverse=True)

        salida["comitentes"].append(
            {
                "comitente": comitente,
                "nombre": datos["nombre"],
                "total": round(total, 2),
                "tenencias": tenencias,
            }
        )

    salida["comitentes"].sort(key=lambda c: c["total"], reverse=True)
    salida["aum_total"] = round(sum(c["total"] for c in salida["comitentes"]), 2)
    salida["cantidad_comitentes"] = len(salida["comitentes"])

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"OK: {salida['cantidad_comitentes']} comitentes, AUM total {salida['aum_total']:,.2f}")


if __name__ == "__main__":
    generar()
