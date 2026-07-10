"""
Genera /data/metricas.json con, para cada ticker de renta variable (Acciones,
Cedear) presente en data/tenencias.json, tres indicadores calculados siempre
en dolares:

  - distancia_sma200_pct: % de distancia entre el precio actual y la media
    movil de 200 ruedas.
  - rendimiento_anual_pct: rendimiento anual promedio (CAGR) sobre los
    ultimos 10 anios de historial disponible (o menos, si el instrumento es
    mas nuevo, o si la serie de conversion a USD es mas corta - se informa
    la cantidad real de anios usada en "anios_usados").
  - distancia_maximo_pct: % de distancia entre el precio actual y el maximo
    historico dentro de la ventana de datos usada (hasta 10 anios).
  - beta: beta de mercado tal como lo publica Yahoo Finance (campo "beta"
    para acciones/ADRs, o "beta3Year" para ETFs/fondos, que es el campo que
    Yahoo usa para ese tipo de instrumento). Null si Yahoo no lo publica
    para ese ticker. No se calcula localmente, es el dato de Yahoo tal cual,
    salvo las excepciones puntuales en BETA_OVERRIDE (ver mas abajo).

  Excepcion: ETHA e IBIT (ETFs de Ether y Bitcoin) tienen "beta3Year"=0.0 en
  Yahoo, pero no porque no se muevan con el mercado sino porque son
  productos demasiado nuevos y todavia no tienen 3 anios de historial para
  que Yahoo lo calcule (artefacto de datos, no una medida real). A pedido
  del usuario, se reemplaza por un valor fijo: ETHA=2.6, IBIT=2.14 (ver
  BETA_OVERRIDE). No es un dato de Yahoo, es una asuncion explicita del
  usuario y se deja documentada aca para que quede clara la diferencia.

Fuentes:
  - Yahoo Finance via la libreria yfinance (sin API key), para precios de
    ADRs, CEDEARs subyacentes y OTC en USD, y para precios en ARS de
    acciones que solo cotizan en BYMA.
  - api.argentinadatos.com (publica, sin API key) para la serie historica
    diaria del dolar MEP ("bolsa"), usada para convertir a USD las acciones
    que solo cotizan en pesos en BYMA (ver MEP_CONVERSION_MAP). Metodologia
    de conversion (dolar MEP vs. CCL vs. oficial) confirmada con el usuario.

Reglas de negocio / mapeo de tickers (verificado, no inventado):
  - Los CEDEARs (tipo "Cedear" en tenencias.json) son casi siempre el mismo
    ticker que el activo subyacente en su mercado de origen. Excepciones
    confirmadas contra el listado oficial de BYMA (CEDEARs Negociables en
    BYMA): "BRKB" -> "BRK-B" (Berkshire Hathaway), "DISN" -> "DIS" (Walt
    Disney Co) y "ADGO" -> "AGRO" (Adecoagro S.A., NYSE, ratio 1:1;
    verificado via busqueda publica + yfinance: currency=USD).
  - Las acciones argentinas locales (tipo "Acciones") se traducen a su ADR
    en dolares cuando existe uno verificado (ver ADR_MAP). BYMA y MOLI no
    tienen ADR pero si cotizan OTC en USD (BYMAF y MOPLF, verificado via
    yfinance fast_info: currency=USD) y se tratan igual que un ADR.
  - TGNO4, TRAN y TXAR no tienen ADR ni cotizan en USD en ningun mercado
    conocido; solo cotizan en pesos en BYMA (TGNO4.BA, TRAN.BA, TXAR.BA,
    verificado currency=ARS via yfinance). Se convierten a USD dividiendo el
    cierre diario en ARS por el dolar MEP del mismo dia (ver
    MEP_CONVERSION_MAP y obtener_serie_mep).
  - PNIZF (FCI Puerto Nizuc) no tiene ningun ticker publico identificado ni
    en Yahoo Finance ni en BYMA. Queda sin_dato_usd=true hasta contar con
    una fuente concreta.

Salida: data/metricas.json
"""

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import yfinance as yf

TENENCIAS_PATH = "data/tenencias.json"
OUTPUT_PATH = "data/metricas.json"

MEP_HISTORICO_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/bolsa"

CEDEAR_EXCEPCIONES = {
    "BRKB": "BRK-B",
    "DISN": "DIS",
    "ADGO": "AGRO",
}

ADR_MAP = {
    "BBAR": "BBAR",
    "BMA": "BMA",
    "CEPU": "CEPU",
    "CRES": "CRESY",
    "EDN": "EDN",
    "GGAL": "GGAL",
    "IRSA": "IRS",
    "LOMA": "LOMA",
    "PAMP": "PAM",
    "SUPV": "SUPV",
    "YPFD": "YPF",
    "BYMA": "BYMAF",
    "MOLI": "MOPLF",
    "PNIZF": None,
    "TGNO4": None,
    "TRAN": None,
    "TXAR": None,
}

MEP_CONVERSION_MAP = {
    "TGNO4": "TGNO4.BA",
    "TRAN": "TRAN.BA",
    "TXAR": "TXAR.BA",
}

SIN_DATO_MOTIVO = {
    "PNIZF": "Fondo cerrado local (FCI Puerto Nizuc); no se encontro ningun ticker publico (ni USD ni ARS).",
}

# Override manual de beta, definido explicitamente por el usuario (NO es un
# dato de Yahoo Finance). Yahoo reporta beta3Year=0.0 para ETHA e IBIT
# porque son ETFs demasiado nuevos y no tienen 3 anios de historial todavia;
# ese 0.0 no refleja sensibilidad real al mercado. Se pisa con estos valores
# fijos hasta que Yahoo tenga historial suficiente para calcularlo.
BETA_OVERRIDE = {
    "ETHA": 2.6,
    "IBIT": 2.14,
}


def get_tickers_renta_variable():
    with open(TENENCIAS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    tickers = {"Acciones": set(), "Cedear": set()}
    for c in data["comitentes"]:
        for t in c["tenencias"]:
            if t["tipo"] in tickers:
                tickers[t["tipo"]].add(t["ticker"])
    return tickers


def armar_universo():
    tickers_por_tipo = get_tickers_renta_variable()
    universo = {}
    for ticker in tickers_por_tipo["Cedear"]:
        universo[ticker] = CEDEAR_EXCEPCIONES.get(ticker, ticker)
    for ticker in tickers_por_tipo["Acciones"]:
        universo[ticker] = ADR_MAP.get(ticker, None)
    return universo


def obtener_beta_individual(ticker_yahoo):
    """Trae el beta publicado por Yahoo Finance para un ticker. Para acciones
    usa el campo "beta"; para ETFs/fondos Yahoo no completa ese campo y usa
    en cambio "beta3Year" (verificado con SPY, QQQ, GLD, EEM). Se intenta
    primero "beta" y si no esta se usa "beta3Year". None si Yahoo no publica
    ninguno de los dos para ese ticker."""
    try:
        info = yf.Ticker(ticker_yahoo).info
    except Exception:
        return ticker_yahoo, None
    beta = info.get("beta")
    if beta is None:
        beta = info.get("beta3Year")
    return ticker_yahoo, beta


def obtener_betas(tickers_yahoo):
    """Trae el beta de una lista de tickers de Yahoo en paralelo (son llamadas
    de red independientes por ticker, no hay endpoint batch para "info").
    Aplica BETA_OVERRIDE al final para los tickers con excepcion manual
    definida por el usuario (ver comentario junto a BETA_OVERRIDE)."""
    resultados = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futuros = {ex.submit(obtener_beta_individual, t): t for t in tickers_yahoo}
        for fut in as_completed(futuros):
            ticker_yahoo, beta = fut.result()
            resultados[ticker_yahoo] = beta
    for ticker_yahoo in resultados:
        if ticker_yahoo in BETA_OVERRIDE:
            resultados[ticker_yahoo] = BETA_OVERRIDE[ticker_yahoo]
    return resultados


def obtener_serie_mep():
    req = urllib.request.Request(MEP_HISTORICO_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        registros = json.loads(resp.read().decode("utf-8"))
    serie = {}
    for r in registros:
        try:
            fecha = datetime.strptime(r["fecha"], "%Y-%m-%d").date()
            serie[fecha] = float(r["venta"])
        except (KeyError, ValueError):
            continue
    return serie


def convertir_a_usd_con_mep(pares_ars, serie_mep):
    if not serie_mep:
        return []
    fechas_mep_ordenadas = sorted(serie_mep.keys())
    primer_fecha_mep = fechas_mep_ordenadas[0]

    resultado = []
    idx = 0
    ultimo_valor_mep = None
    for fecha, precio_ars in pares_ars:
        if fecha < primer_fecha_mep:
            continue
        while idx < len(fechas_mep_ordenadas) and fechas_mep_ordenadas[idx] <= fecha:
            ultimo_valor_mep = serie_mep[fechas_mep_ordenadas[idx]]
            idx += 1
        if ultimo_valor_mep is None or ultimo_valor_mep <= 0:
            continue
        resultado.append((fecha, precio_ars / ultimo_valor_mep))
    return resultado


def calcular_metricas_de_serie(cierre_por_fecha):
    if not cierre_por_fecha:
        return None
    fechas = [f for f, _ in cierre_por_fecha]
    precios = [p for _, p in cierre_por_fecha]

    precio_actual = precios[-1]
    fecha_inicio, fecha_fin = fechas[0], fechas[-1]
    dias = (fecha_fin - fecha_inicio).days
    anios_usados = round(dias / 365, 2) if dias > 0 else 0.0

    cagr_pct = None
    if precios[0] and precios[0] > 0 and dias > 0:
        cagr_pct = ((precio_actual / precios[0]) ** (365.0 / dias) - 1) * 100

    maximo = max(precios)
    distancia_maximo_pct = (precio_actual - maximo) / maximo * 100 if maximo > 0 else None

    sma200 = None
    distancia_sma200_pct = None
    if len(precios) >= 200:
        sma200 = sum(precios[-200:]) / 200
        if sma200 > 0:
            distancia_sma200_pct = (precio_actual - sma200) / sma200 * 100

    return {
        "precio_actual_usd": round(precio_actual, 4),
        "sma200_usd": round(sma200, 4) if sma200 is not None else None,
        "distancia_sma200_pct": round(distancia_sma200_pct, 2) if distancia_sma200_pct is not None else None,
        "rendimiento_anual_pct": round(cagr_pct, 2) if cagr_pct is not None else None,
        "anios_usados": anios_usados,
        "maximo_periodo_usd": round(maximo, 4),
        "distancia_maximo_pct": round(distancia_maximo_pct, 2) if distancia_maximo_pct is not None else None,
    }


def procesar_circuito_usd_directo(universo, salida):
    tickers_yahoo = sorted({v for v in universo.values() if v is not None})
    if not tickers_yahoo:
        return

    datos = yf.download(
        tickers_yahoo, period="10y", group_by="ticker", threads=True, progress=False, auto_adjust=False
    )
    multi = len(tickers_yahoo) > 1
    betas = obtener_betas(tickers_yahoo)

    for ticker_yahoo in tickers_yahoo:
        try:
            serie = datos[ticker_yahoo]["Close"].dropna() if multi else datos["Close"].dropna()
            pares = [(idx.date(), float(val)) for idx, val in serie.items()]
        except Exception:
            pares = []
        if not pares:
            for ticker_local, ty in universo.items():
                if ty == ticker_yahoo:
                    salida["tickers"][ticker_local] = {
                        "sin_dato_usd": True,
                        "motivo": "Sin datos de Yahoo Finance para " + ticker_yahoo + ".",
                    }
            continue
        metricas = calcular_metricas_de_serie(pares)
        metricas["ticker_usd"] = ticker_yahoo
        metricas["beta"] = betas.get(ticker_yahoo)
        for ticker_local, ty in universo.items():
            if ty == ticker_yahoo:
                salida["tickers"][ticker_local] = metricas


def procesar_circuito_mep(salida):
    if not MEP_CONVERSION_MAP:
        return
    try:
        serie_mep = obtener_serie_mep()
    except Exception as e:
        for ticker_local in MEP_CONVERSION_MAP:
            salida["tickers"][ticker_local] = {
                "sin_dato_usd": True,
                "motivo": "No se pudo obtener la serie historica del dolar MEP: " + str(e),
            }
        return

    tickers_ba = sorted(set(MEP_CONVERSION_MAP.values()))
    datos = yf.download(
        tickers_ba, period="10y", group_by="ticker", threads=True, progress=False, auto_adjust=False
    )
    multi = len(tickers_ba) > 1
    betas = obtener_betas(tickers_ba)

    for ticker_local, ticker_ba in MEP_CONVERSION_MAP.items():
        try:
            serie = datos[ticker_ba]["Close"].dropna() if multi else datos["Close"].dropna()
            pares_ars = [(idx.date(), float(val)) for idx, val in serie.items()]
        except Exception:
            pares_ars = []
        if not pares_ars:
            salida["tickers"][ticker_local] = {
                "sin_dato_usd": True,
                "motivo": "Sin datos de Yahoo Finance para " + ticker_ba + ".",
            }
            continue
        pares_usd = convertir_a_usd_con_mep(pares_ars, serie_mep)
        if not pares_usd:
            salida["tickers"][ticker_local] = {
                "sin_dato_usd": True,
                "motivo": "No hay superposicion entre el historial de precios y la serie de dolar MEP disponible.",
            }
            continue
        metricas = calcular_metricas_de_serie(pares_usd)
        metricas["ticker_usd"] = ticker_ba
        metricas["conversion"] = "ARS -> USD via dolar MEP diario (api.argentinadatos.com)"
        metricas["beta"] = betas.get(ticker_ba)
        salida["tickers"][ticker_local] = metricas


def generar():
    universo = armar_universo()
    salida = {"tickers": {}}

    procesar_circuito_usd_directo(universo, salida)
    procesar_circuito_mep(salida)

    for ticker_local, ty in universo.items():
        if ty is None and ticker_local not in MEP_CONVERSION_MAP and ticker_local not in salida["tickers"]:
            salida["tickers"][ticker_local] = {
                "sin_dato_usd": True,
                "motivo": SIN_DATO_MOTIVO.get(ticker_local, "Sin ADR/ticker en dolares disponible."),
            }

    salida["generado"] = datetime.now(timezone.utc).isoformat()
    salida["metodologia"] = {
        "distancia_sma200_pct": "Distancia % entre precio actual y media movil de 200 ruedas.",
        "rendimiento_anual_pct": "CAGR anualizado sobre hasta 10 anios de historial disponible (ver anios_usados por ticker).",
        "distancia_maximo_pct": "Distancia % entre precio actual y el maximo de cierre dentro de la ventana de datos usada.",
        "moneda": "Todos los valores en dolares estadounidenses (USD).",
        "fuente": "Yahoo Finance (via yfinance) para precios; api.argentinadatos.com para el dolar MEP historico usado en la conversion de TGNO4, TRAN y TXAR.",
        "conversion_mep": "TGNO4, TRAN y TXAR cotizan solo en pesos en BYMA; se convierten a USD dividiendo el cierre diario en ARS por el dolar MEP (venta) del mismo dia.",
        "beta": "Beta publicado por Yahoo Finance (campo 'beta' para acciones/ADRs, 'beta3Year' para ETFs/fondos). Null si Yahoo no lo publica para ese ticker.",
        "beta_override": "ETHA e IBIT usan un beta fijo definido por el usuario (2.6 y 2.14) en vez del beta3Year=0.0 de Yahoo, que es un artefacto por falta de 3 anios de historial y no una medida real.",
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    ok = sum(1 for v in salida["tickers"].values() if not v.get("sin_dato_usd"))
    sin_dato = len(salida["tickers"]) - ok
    print("OK: " + str(ok) + " con datos, " + str(sin_dato) + " sin dato USD. Total tickers: " + str(len(salida["tickers"])))
    for ticker_local, v in salida["tickers"].items():
        if v.get("sin_dato_usd"):
            print("  sin dato: " + ticker_local + " -> " + str(v.get("motivo")))


if __name__ == "__main__":
    generar()
