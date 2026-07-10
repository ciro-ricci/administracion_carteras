"""
Genera /data/renta_fija.json con la TIR (en dolares) de cada instrumento de
renta fija en cartera (tipos "ON", "Bonos" y "Letras" en tenencias.json).

Fuente principal: la misma Google Sheet publica de seguimiento de ONs y
Bonos que usa el usuario:

  https://docs.google.com/spreadsheets/d/1y8OXueedijhun4d-v2yz8KcimRR58mQSmzs0t1CKUjg
  (gid=387376027)

La planilla trae, entre otras columnas, "TickerARS" (codigo de la especie
tal como opera en pesos en el mercado local) y "TIR desde USD" (columna K).
Cubre tanto ONs ("Especie" = "ON USD") como Bonos soberanos/subsoberanos
("Especie" = "Bonos USD" / "Subsoberanos USD").

Regla de negocio para los que NO aparecen en la planilla, o cuya celda de
TIR esta vacia/"#N/A" (regla fija definida explicitamente por el usuario,
NO inventada por este script):
  - ONs sin dato en la planilla: TIR fija de 6% (default_6pct).
  - Bonos sin dato en la planilla (mayormente Boncer / dollar-linked, ej.
    TZX, TX, TXMJ, DICP, CUAP, PARP, T15E7, T30A7, TTD26, TTS26, TMF27,
    TZXA7, TX26, TO26): TIR fija de 6% (default_6pct).
  - Letras / LECAPs: siempre TIR fija de 6% (default_6pct), no se busca
    fuente para letras.

Cada ticker en la salida indica "fuente": "sheet" (dato real de mercado) o
"default_6pct" (regla fija del usuario), para que quede siempre claro cual
es cual y no se confunda un dato de mercado con una asuncion.

Salida: data/renta_fija.json
"""

import csv
import io
import json
import urllib.request
from datetime import datetime, timezone

TENENCIAS_PATH = "data/tenencias.json"
OUTPUT_PATH = "data/renta_fija.json"

SHEET_ID = "1y8OXueedijhun4d-v2yz8KcimRR58mQSmzs0t1CKUjg"
GID = "387376027"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"

TIPOS_RENTA_FIJA = {"ON", "Bonos", "Letras"}

TIR_DEFAULT_PCT = 6.0


def get_tickers_renta_fija():
    with open(TENENCIAS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    tickers = {tipo: set() for tipo in TIPOS_RENTA_FIJA}
    for c in data["comitentes"]:
        for t in c["tenencias"]:
            if t["tipo"] in TIPOS_RENTA_FIJA:
                tickers[t["tipo"]].add(t["ticker"])
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
    tickers_por_tipo = get_tickers_renta_fija()
    filas = descargar_csv(CSV_URL)

    por_ticker_ars = {}
    for fila in filas:
        ticker_ars = (fila.get("TickerARS") or "").strip()
        if ticker_ars:
            por_ticker_ars[ticker_ars] = fila

    salida = {"tickers": {}}

    for tipo, tickers in tickers_por_tipo.items():
        for ticker in sorted(tickers):
            if tipo == "Letras":
                salida["tickers"][ticker] = {
                    "tipo": tipo,
                    "tir_usd_pct": TIR_DEFAULT_PCT,
                    "fuente": "default_6pct",
                    "motivo": "Letra/LECAP: no se conecta fuente de TIR para letras; se aplica la regla fija de 6% definida por el usuario.",
                }
                continue

            fila = por_ticker_ars.get(ticker)
            if fila is None:
                salida["tickers"][ticker] = {
                    "tipo": tipo,
                    "tir_usd_pct": TIR_DEFAULT_PCT,
                    "fuente": "default_6pct",
                    "motivo": "El ticker no aparece en la planilla de seguimiento de ONs/Bonos; se aplica la regla fija de 6% definida por el usuario.",
                }
                continue

            tir_usd = parse_pct(fila.get("TIR desde USD", ""))
            if tir_usd is None:
                salida["tickers"][ticker] = {
                    "tipo": tipo,
                    "tir_usd_pct": TIR_DEFAULT_PCT,
                    "fuente": "default_6pct",
                    "motivo": "TIR desde USD sin dato (#N/A o vacio) en la planilla; se aplica la regla fija de 6% definida por el usuario.",
                }
            else:
                salida["tickers"][ticker] = {
                    "tipo": tipo,
                    "tir_usd_pct": tir_usd,
                    "fuente": "sheet",
                    "empresa": (fila.get("Empresa") or "").strip() or None,
                    "vencimiento": (fila.get("Vto.") or "").strip() or None,
                }

    salida["generado"] = datetime.now(timezone.utc).isoformat()
    salida["metodologia"] = {
        "fuente_principal": "Google Sheet publica de seguimiento de ONs y Bonos (columna 'TIR desde USD').",
        "regla_default": f"Para ONs, Bonos y Letras sin TIR de mercado disponible, se usa una TIR fija de {TIR_DEFAULT_PCT}% (regla definida explicitamente por el usuario, no calculada).",
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    total = len(salida["tickers"])
    de_sheet = sum(1 for v in salida["tickers"].values() if v.get("fuente") == "sheet")
    default = total - de_sheet
    print(f"OK: {total} tickers de renta fija ({de_sheet} con TIR de mercado, {default} con default 6%).")
    for tipo in sorted(TIPOS_RENTA_FIJA):
        n = sum(1 for v in salida["tickers"].values() if v.get("tipo") == tipo)
        n_sheet = sum(1 for v in salida["tickers"].values() if v.get("tipo") == tipo and v.get("fuente") == "sheet")
        print(f"  {tipo}: {n} tickers ({n_sheet} de la sheet, {n - n_sheet} default 6%)")


if __name__ == "__main__":
    generar()
