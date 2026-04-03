"""
Tupi API Explorer
=================
Testa e documenta todos os endpoints descobertos da API da Tupinamb Energy.
Roda com: python tupi_api_explorer.py
Dependências: pip install httpx rich
"""

import httpx
import json
from datetime import datetime

BASE_URL = "https://api.tupinambaenergia.com.br"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36",
    "Accept": "application/json, */*",
    "Origin": "https://tupimob.com",
    "Referer": "https://tupimob.com/",
}

# ─── Endpoints a testar ────────────────────────────────────────────────────────

STATION_ENDPOINTS = [
    {
        "label": "Todas as estações (Tipo 2 + CCS 2 + CHAdeMO, AC+DC)",
        "url": f"{BASE_URL}/stationsShortVersion",
        "params": {
            "plugTypes": '["Tipo 2","CCS 2","CHAdeMO"]',
            "fast": "false",
            "searchText": "",
        },
    },
    {
        "label": "Somente carregadores rápidos DC",
        "url": f"{BASE_URL}/stationsShortVersion",
        "params": {
            "plugTypes": '["CCS 2","CHAdeMO"]',
            "fast": "true",
            "searchText": "",
        },
    },
    {
        "label": "Somente AC (Tipo 2)",
        "url": f"{BASE_URL}/stationsShortVersion",
        "params": {
            "plugTypes": '["Tipo 2"]',
            "fast": "false",
            "searchText": "",
        },
    },
    {
        "label": "Sem filtro de plug",
        "url": f"{BASE_URL}/stationsShortVersion",
        "params": {
            "plugTypes": "[]",
            "fast": "false",
            "searchText": "",
        },
    },
    {
        "label": "Busca por texto: São Paulo",
        "url": f"{BASE_URL}/stationsShortVersion",
        "params": {
            "plugTypes": '["Tipo 2","CCS 2","CHAdeMO"]',
            "fast": "false",
            "searchText": "São Paulo",
        },
    },
    {
        "label": "Busca por texto: Shell",
        "url": f"{BASE_URL}/stationsShortVersion",
        "params": {
            "plugTypes": '["Tipo 2","CCS 2","CHAdeMO"]',
            "fast": "false",
            "searchText": "Shell",
        },
    },
]

# Endpoints alternativos (path discovery)
OTHER_ENDPOINTS = [
    {"label": "GET /stations",          "url": f"{BASE_URL}/stations"},
    {"label": "GET /chargers",          "url": f"{BASE_URL}/chargers"},
    {"label": "GET /sessions",          "url": f"{BASE_URL}/sessions"},
    {"label": "GET /availability",      "url": f"{BASE_URL}/availability"},
    {"label": "GET /companies",         "url": f"{BASE_URL}/companies"},
    {"label": "GET /plugTypes",         "url": f"{BASE_URL}/plugTypes"},
    {"label": "GET /stationTypes",      "url": f"{BASE_URL}/stationTypes"},
    {"label": "GET /v1/stations",       "url": f"{BASE_URL}/v1/stations"},
    {"label": "GET /api/stations",      "url": f"{BASE_URL}/api/stations"},
    {"label": "GET /healthz",           "url": f"{BASE_URL}/healthz"},
    {"label": "GET /health",            "url": f"{BASE_URL}/health"},
    {"label": "GET /stationsFullVersion","url": f"{BASE_URL}/stationsFullVersion", "params": {"plugTypes": '["Tipo 2","CCS 2","CHAdeMO"]', "fast": "false", "searchText": ""}},
]


# ─── Helpers ───────────────────────────────────────────────────────────────────

def fmt_sep(char="─", width=70):
    return char * width

def summarize_stations(data: list) -> dict:
    """Extrai estatísticas do array de estações."""
    if not isinstance(data, list) or not data:
        return {}

    total = len(data)
    states = {}
    plug_types = {}
    currents = {}
    private_count = 0
    companies = set()
    charging_now = []

    for s in data:
        # status
        state = s.get("stateName", "Unknown")
        states[state] = states.get(state, 0) + 1

        # privado
        if s.get("private"):
            private_count += 1

        # empresa
        if s.get("parentCompanyID"):
            companies.add(s["parentCompanyID"])

        # corrente
        curr = s.get("current", "?")
        currents[curr] = currents.get(curr, 0) + 1

        # sessão ativa agora
        if state == "Charging":
            charging_now.append({
                "name": s.get("name"),
                "started": s.get("startChargingOn"),
                "expires": s.get("startExpiresOn"),
            })

        # plugs
        for p in s.get("connectedPlugs", []):
            pname = p.get("name", "?")
            plug_types[pname] = plug_types.get(pname, 0) + 1

    return {
        "total_stations": total,
        "estados": states,
        "correntes": currents,
        "tipos_de_plug": plug_types,
        "privadas": private_count,
        "publicas": total - private_count,
        "empresas_unicas": len(companies),
        "carregando_agora": charging_now,
    }


def test_endpoint(client: httpx.Client, label: str, url: str, params: dict = None) -> dict:
    result = {
        "label": label,
        "url": url,
        "params": params,
        "status": None,
        "response_type": None,
        "item_count": None,
        "sample": None,
        "summary": None,
        "error": None,
    }
    try:
        r = client.get(url, params=params, timeout=15)
        result["status"] = r.status_code

        if r.status_code == 200:
            try:
                data = r.json()
                result["response_type"] = type(data).__name__

                if isinstance(data, list):
                    result["item_count"] = len(data)
                    result["sample"] = data[0] if data else None
                    result["summary"] = summarize_stations(data)
                elif isinstance(data, dict):
                    result["item_count"] = len(data.keys())
                    result["sample"] = data
            except Exception as e:
                result["response_type"] = "text"
                result["sample"] = r.text[:300]
        else:
            result["error"] = r.text[:200]

    except httpx.TimeoutException:
        result["error"] = "TIMEOUT"
    except Exception as e:
        result["error"] = str(e)

    return result


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(fmt_sep("═"))
    print(f"  TUPI API EXPLORER — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(fmt_sep("═"))

    results = []

    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:

        # ── 1. Testar endpoints principais ──────────────────────────────────
        print("\n📡 TESTANDO ENDPOINTS DE ESTAÇÕES\n" + fmt_sep())
        for ep in STATION_ENDPOINTS:
            r = test_endpoint(client, ep["label"], ep["url"], ep.get("params"))
            results.append(r)

            status_icon = "✅" if r["status"] == 200 else "❌"
            print(f"\n{status_icon} {r['label']}")
            print(f"   URL    : {r['url']}")
            print(f"   Params : {r['params']}")
            print(f"   Status : {r['status']}")

            if r["status"] == 200:
                print(f"   Tipo   : {r['response_type']}")
                print(f"   Items  : {r['item_count']}")
                if r["summary"]:
                    s = r["summary"]
                    print(f"   ┌─ Total estações   : {s['total_stations']}")
                    print(f"   ├─ Públicas         : {s['publicas']}")
                    print(f"   ├─ Privadas         : {s['privadas']}")
                    print(f"   ├─ Empresas únicas  : {s['empresas_unicas']}")
                    print(f"   ├─ Correntes        : {s['correntes']}")
                    print(f"   ├─ Status           : {s['estados']}")
                    print(f"   ├─ Tipos de plug    : {s['tipos_de_plug']}")
                    charging = s["carregando_agora"]
                    print(f"   └─ Carregando AGORA : {len(charging)} estação(ões)")
                    for c in charging[:3]:
                        print(f"        • {c['name']} | início: {c['started']}")
            else:
                print(f"   Erro   : {r['error']}")

        # ── 2. Testar discovery de outros endpoints ──────────────────────────
        print("\n\n🔍 DISCOVERY DE OUTROS ENDPOINTS\n" + fmt_sep())
        for ep in OTHER_ENDPOINTS:
            r = test_endpoint(client, ep["label"], ep["url"], ep.get("params"))
            results.append(r)
            status_icon = "✅" if r["status"] == 200 else ("⚠️ " if r["status"] and r["status"] < 500 else "❌")
            detail = f"items={r['item_count']}" if r["item_count"] is not None else (r["error"] or "")
            print(f"  {status_icon} [{r['status']}] {r['label']} — {detail}")

        # ── 3. Testar endpoint de estação individual (com ID da response) ────
        print("\n\n🏪 TESTANDO ENDPOINT DE ESTAÇÃO INDIVIDUAL\n" + fmt_sep())

        # Pega um _id real da primeira response bem-sucedida
        first_id = None
        first_station_id = None
        for r in results:
            if r["status"] == 200 and r["sample"] and isinstance(r["sample"], dict):
                first_id = r["sample"].get("_id")
                first_station_id = r["sample"].get("stationID")
                break

        if first_id:
            for path in [f"/stations/{first_id}", f"/station/{first_id}",
                         f"/stationsShortVersion/{first_id}", f"/charger/{first_id}",
                         f"/stations/{first_station_id}"]:
                r = test_endpoint(client, f"GET {path}", f"{BASE_URL}{path}")
                status_icon = "✅" if r["status"] == 200 else "❌"
                print(f"  {status_icon} [{r['status']}] {path}")
                if r["status"] == 200 and r["sample"]:
                    extra_fields = [k for k in r["sample"].keys()
                                    if k not in ("_id","name","lat","lng","stateName","connectedPlugs")]
                    print(f"       Campos extras: {extra_fields}")
        else:
            print("  ⚠️  Nenhum _id disponível para teste individual")

        # ── 4. Salvar JSON completo ─────────────────────────────────────────
        output_file = "tupi_api_results.json"
        with open(output_file, "w", encoding="utf-8") as f:
            # Salva só o resultado da primeira request bem-sucedida com dados completos
            full_data = None
            for r in results:
                if r["status"] == 200 and r["item_count"] and r["item_count"] > 10:
                    # re-fetch para pegar dados completos (não só sample)
                    try:
                        resp = client.get(r["url"], params=r["params"], timeout=20)
                        full_data = resp.json()
                        break
                    except:
                        pass
            json.dump(full_data or [], f, ensure_ascii=False, indent=2)

        print(f"\n\n💾 Dados completos salvos em: {output_file}")

    # ── Resumo final ────────────────────────────────────────────────────────
    print("\n" + fmt_sep("═"))
    print("  RESUMO FINAL")
    print(fmt_sep("═"))
    ok = [r for r in results if r["status"] == 200]
    fail = [r for r in results if r["status"] != 200]
    print(f"  ✅ Endpoints funcionando : {len(ok)}")
    print(f"  ❌ Endpoints com erro    : {len(fail)}")
    if ok:
        print("\n  Endpoints disponíveis:")
        for r in ok:
            print(f"    • {r['label']} → {r['item_count']} items")
    print(fmt_sep("═"))


if __name__ == "__main__":
    main()