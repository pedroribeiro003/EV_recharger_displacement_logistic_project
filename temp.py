import io, json, sys, time
from datetime import datetime, timedelta, timezone
import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_URL = "https://api.tupinambaenergia.com.br"
ORIGIN   = "https://tupimob.com"
PLUG_TYPES = ["Tipo 2", "CCS 2", "CHAdeMO"]
HEADERS = {
    "Origin": ORIGIN,
    "Referer": ORIGIN + "/",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
client = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=20, follow_redirects=True)
SEP = "-" * 65


def probe(method, path, **kwargs):
    try:
        resp = getattr(client, method)(path, **kwargs)
        return resp.status_code, resp.text[:600]
    except httpx.TimeoutException:
        return -1, "TIMEOUT"
    except Exception as exc:
        return -2, str(exc)


def report(label, status, body):
    if status == 200:
        print(f"  OK [{status}]  {label}")
        print(f"         -> {body[:400]}")
        print()
    elif status not in (404, 405, 422, -1, -2):
        print(f"  ?? [{status}]  {label}  ->  {body[:120]}")
    else:
        print(f"   . [{status}]  {label}")


# ---------- STEP 1: pegar IDs reais ----------
print(SEP)
print("STEP 1 - buscando IDs reais de estacoes")
print(SEP)

_qs = {"plugTypes": json.dumps(PLUG_TYPES), "fast": "false", "searchText": ""}
status, body = probe("get", "/stationsShortVersion", params=_qs)
print(f"  GET /stationsShortVersion -> {status}")
if status != 200:
    print("  Falhou - encerrando.")
    sys.exit(1)

stations_raw = client.get("/stationsShortVersion", params=_qs).json()
if isinstance(stations_raw, dict):
    stations_raw = stations_raw.get("stations") or stations_raw.get("data") or []

print(f"  Total de estacoes: {len(stations_raw)}")

samples = stations_raw[:5]
station_ids = [str(s.get("_id") or s.get("id") or s.get("stationId") or "") for s in samples]
station_ids = [x for x in station_ids if x]
station_code_ids = [str(s.get("stationID") or s.get("station_id") or "") for s in samples]
station_code_ids = [x for x in station_code_ids if x]
print(f"  _id samples:       {station_ids}")
print(f"  stationID samples: {station_code_ids}")

connector_ids = []
if samples:
    conns = (samples[0].get("connectors") or samples[0].get("plugs")
             or samples[0].get("connectedPlugs") or [])
    connector_ids = [str(c.get("id") or c.get("connectorId") or c.get("_id") or "")
                     for c in conns if c]
    connector_ids = [x for x in connector_ids if x]
print(f"  Connector IDs (posto 0): {connector_ids}")

print(f"\n  Chaves do posto[0]: {list(samples[0].keys()) if samples else '--'}")
if samples:
    for k, v in samples[0].items():
        print(f"    {k}: {str(v)[:100]}")

sid  = station_ids[0]       if station_ids       else "1"
scid = station_code_ids[0]  if station_code_ids  else "CPHT001"
cid  = connector_ids[0]     if connector_ids     else "1"

now       = datetime.now(timezone.utc)
since     = (now - timedelta(days=30)).strftime("%Y-%m-%d")
until     = now.strftime("%Y-%m-%d")
since_iso = (now - timedelta(days=30)).isoformat()
until_iso = now.isoformat()
print()

# ---------- STEP 2: endpoints globais ----------
print(SEP)
print("STEP 2 - endpoints globais de historico")
print(SEP)

global_candidates = [
    ("get",  "/sessions",            {}),
    ("get",  "/transactions",        {}),
    ("get",  "/history",             {}),
    ("get",  "/chargeHistory",       {}),
    ("get",  "/chargeSessions",      {}),
    ("get",  "/charging-sessions",   {}),
    ("get",  "/charging-history",    {}),
    ("get",  "/events",              {}),
    ("get",  "/logs",                {}),
    ("get",  "/availability",        {}),
    ("get",  "/availabilityHistory", {}),
    ("get",  "/occupancy",           {}),
    ("get",  "/occupancyHistory",    {}),
    ("get",  "/status/history",      {}),
    ("get",  "/reports",             {}),
    ("get",  "/analytics",           {}),
    ("get",  "/statistics",          {}),
    ("get",  "/stats",               {}),
    ("get",  "/metrics",             {}),
    ("get",  "/stationHistory",      {}),
    ("get",  "/stationsHistory",     {}),
    ("get",  "/stationsFullVersion", {"params": {"plugTypes": json.dumps(PLUG_TYPES), "fast": "false"}}),
    ("get",  "/connector/history",   {}),
    ("post", "/history",             {"json": {"from": since, "to": until}}),
    ("post", "/sessions",            {"json": {"from": since, "to": until}}),
    ("post", "/chargeSessions",      {"json": {"from": since, "to": until}}),
]

for method, path, kwargs in global_candidates:
    s, b = probe(method, path, **kwargs)
    report(f"{method.upper()} {path}", s, b)
    time.sleep(0.12)

# ---------- STEP 3: por estacao (usando _id e stationID) ----------
for label, station_ref in [("_id", sid), ("stationID", scid)]:
    print(SEP)
    print(f"STEP 3 - por estacao [{label}={station_ref}]")
    print(SEP)

    per_station = [
        ("get",  f"/station/{station_ref}/history",            {}),
        ("get",  f"/station/{station_ref}/sessions",           {}),
        ("get",  f"/station/{station_ref}/transactions",       {}),
        ("get",  f"/station/{station_ref}/events",             {}),
        ("get",  f"/station/{station_ref}/logs",               {}),
        ("get",  f"/station/{station_ref}/status/history",     {}),
        ("get",  f"/station/{station_ref}/availability",       {}),
        ("get",  f"/station/{station_ref}/chargeHistory",      {}),
        ("get",  f"/station/{station_ref}/chargeSessions",     {}),
        ("get",  f"/station/{station_ref}/charging-sessions",  {}),
        ("get",  f"/station/{station_ref}/reports",            {}),
        ("get",  f"/stations/{station_ref}/history",           {}),
        ("get",  f"/stations/{station_ref}/sessions",          {}),
        ("get",  f"/station/{station_ref}/history",  {"params": {"from": since, "to": until}}),
        ("get",  f"/station/{station_ref}/sessions", {"params": {"from": since, "to": until}}),
        ("get",  f"/station/{station_ref}/history",  {"params": {"startDate": since_iso, "endDate": until_iso}}),
        ("post", f"/station/{station_ref}/history",  {"json": {"from": since, "to": until}}),
        ("post", f"/station/{station_ref}/sessions", {"json": {"from": since, "to": until}}),
    ]
    for method, path, kwargs in per_station:
        s, b = probe(method, path, **kwargs)
        report(f"{method.upper()} {path}", s, b)
        time.sleep(0.12)

# ---------- STEP 4: por connector ----------
if cid:
    print(SEP)
    print(f"STEP 4 - por connector (cid={cid})")
    print(SEP)
    per_conn = [
        ("get", f"/station/{sid}/connector/{cid}/history",       {}),
        ("get", f"/station/{sid}/connector/{cid}/sessions",      {}),
        ("get", f"/station/{sid}/connector/{cid}/transactions",  {}),
        ("get", f"/connector/{cid}/history",                     {}),
        ("get", f"/connector/{cid}/sessions",                    {}),
        ("get", f"/connectors/{cid}/history",                    {}),
        ("get", f"/connector/{cid}/history",  {"params": {"from": since, "to": until}}),
        ("get", f"/connector/{cid}/sessions", {"params": {"from": since, "to": until}}),
    ]
    for method, path, kwargs in per_conn:
        s, b = probe(method, path, **kwargs)
        report(f"{method.upper()} {path}", s, b)
        time.sleep(0.12)

# ---------- STEP 5: detalhe completo ----------
print(SEP)
print(f"STEP 5 - detalhe completo: GET /station/{sid}")
print(SEP)
s, b = probe("get", f"/station/{sid}")
print(f"  status: {s}")
if s == 200:
    try:
        detail = client.get(f"/station/{sid}").json()
        if isinstance(detail, dict):
            print(f"  Chaves: {list(detail.keys())}")
            for k, v in detail.items():
                print(f"    {k}: {str(v)[:150]}")
        else:
            print(f"  Tipo: {type(detail)} -> {str(detail)[:300]}")
    except Exception as e:
        print(f"  Parse error: {e}")

# ---------- STEP 6: endpoints que exigem auth ----------
print()
print(SEP)
print("STEP 6 - endpoints que podem exigir autenticacao (401/403)")
print(SEP)
auth_candidates = [
    ("get", "/user/sessions"),
    ("get", "/user/history"),
    ("get", "/me/sessions"),
    ("get", "/me/history"),
    ("get", "/account/sessions"),
    ("get", "/admin/sessions"),
    ("get", "/admin/history"),
    ("get", "/dashboard"),
    ("get", "/dashboard/history"),
    ("get", "/operator/sessions"),
    ("get", "/operator/history"),
]
for method, path in auth_candidates:
    s, b = probe(method, path)
    if s in (401, 403):
        print(f"  !! [{s}] {method.upper()} {path}  <- REQUER AUTH (endpoint existe!)")
    elif s == 200:
        report(f"{method.upper()} {path}", s, b)
    else:
        print(f"   . [{s}] {method.upper()} {path}")
    time.sleep(0.12)

print()
print(SEP)
print("DONE")
print(SEP)
