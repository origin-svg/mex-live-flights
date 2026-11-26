#!/usr/bin/env python3
"""Consulta aviones cerca de MEX (MMMX) en feeds ADS-B comunitarios, los imprime
en el log en cada consulta y publica latest.json + history.json en la rama `data`
(un solo commit, se sobrescribe)."""
import json, os, subprocess, sys, time, urllib.request
from datetime import datetime, timezone

LAT = float(os.environ.get("LAT", "19.4363"))        # MMMX
LON = float(os.environ.get("LON", "-99.0721"))
RADIUS_NM = int(float(os.environ.get("RADIUS_NM", "60")))
DURATION_S = int(os.environ.get("DURATION_S", "270"))
INTERVAL_S = int(os.environ.get("INTERVAL_S", "20"))
PUSH_EVERY = int(os.environ.get("PUSH_EVERY", "3"))   # publicar cada N consultas
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "90"))
DATA_DIR = os.environ.get("DATA_DIR", "data")
PUSH = os.environ.get("PUSH", "1") == "1"
PRINT_MAX = int(os.environ.get("PRINT_MAX", "60"))
UA = "mex-flights-relay (GitHub Actions; 1 req/%ss)" % INTERVAL_S

SOURCES = [
    ("adsb.lol", "https://api.adsb.lol/v2/point/{lat}/{lon}/{r}"),
    ("airplanes.live", "https://api.airplanes.live/v2/point/{lat}/{lon}/{r}"),
    ("adsb.fi", "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{r}"),
]


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def normalize(a):
    if a.get("lat") is None or a.get("lon") is None:
        return None
    alt = a.get("alt_baro")
    ground = alt == "ground"
    return {
        "hex": a.get("hex"),
        "callsign": (a.get("flight") or "").strip() or None,
        "reg": a.get("r"),
        "type": a.get("t"),
        "lat": a.get("lat"),
        "lon": a.get("lon"),
        "alt_ft": 0 if ground else alt,
        "on_ground": ground,
        "gs_kt": a.get("gs"),
        "track": a.get("track"),
        "vs_fpm": a.get("baro_rate"),
        "squawk": a.get("squawk"),
        "seen_s": a.get("seen"),
    }


def snapshot():
    last_err = None
    for name, tpl in SOURCES:
        url = tpl.format(lat=LAT, lon=LON, r=RADIUS_NM)
        try:
            data = fetch_json(url)
            raw = data.get("ac") or data.get("aircraft") or []
            ac = [x for x in (normalize(a) for a in raw) if x]
            return name, ac
        except Exception as e:  # noqa
            last_err = "%s: %s" % (name, e)
            print("fuente fallo ->", last_err, file=sys.stderr, flush=True)
    raise RuntimeError(last_err or "sin fuentes")


def load_history():
    try:
        with open(os.path.join(DATA_DIR, "history.json")) as f:
            return json.load(f)
    except Exception:
        return []


def write_files(source, ac, history):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(DATA_DIR, exist_ok=True)
    latest = {"updated_utc": now, "source": source,
              "center": {"lat": LAT, "lon": LON, "name": "MMMX"},
              "radius_nm": RADIUS_NM, "count": len(ac), "aircraft": ac}
    history.append({"t": now, "ac": [[a["hex"], a["callsign"], a["lat"], a["lon"],
                                      a["alt_ft"], a["gs_kt"], a["track"]] for a in ac]})
    del history[:-MAX_HISTORY]
    with open(os.path.join(DATA_DIR, "latest.json"), "w") as f:
        json.dump(latest, f, separators=(",", ":"))
    with open(os.path.join(DATA_DIR, "history.json"), "w") as f:
        json.dump({"fields": ["hex", "callsign", "lat", "lon", "alt_ft", "gs_kt", "track"],
                   "snapshots": history}, f, separators=(",", ":"))


def print_table(source, ac):
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print("=== %s UTC | %s | %d aviones ===" % (stamp, source, len(ac)), flush=True)
    for a in sorted(ac, key=lambda x: -(x["alt_ft"] or 0))[:PRINT_MAX]:
        nombre = a["callsign"] or a["hex"]
        tierra = "  [tierra]" if a["on_ground"] else ""
        print(f"{nombre:<8} {(a['type'] or '-'):<5} alt {str(a['alt_ft']):>6} ft  "
              f"{str(a['gs_kt']):>4} kt  rumbo {str(a['track']):>5}  "
              f"{a['lat']:.3f},{a['lon']:.3f}{tierra}", flush=True)


def git(*args):
    return subprocess.run(["git", "-C", DATA_DIR, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def push():
    git("add", "-A")
    tree = git("write-tree")
    commit = git("commit-tree", tree, "-m", "snapshot")   # commit huerfano: el repo no crece
    git("push", "--force", "origin", "%s:refs/heads/data" % commit)
    print("publicado", commit[:7], flush=True)


def main():
    hist = load_history()
    if isinstance(hist, dict):
        hist = hist.get("snapshots", [])
    start, n, ok = time.time(), 0, 0
    while True:
        try:
            source, ac = snapshot()
            write_files(source, ac, hist)
            ok += 1
            print_table(source, ac)
        except Exception as e:  # noqa
            print("consulta fallo:", e, file=sys.stderr, flush=True)
        n += 1
        if PUSH and ok and n % PUSH_EVERY == 0:
            try:
                push()
            except Exception as e:  # noqa
                print("push fallo:", e, file=sys.stderr, flush=True)
        if time.time() - start + INTERVAL_S > DURATION_S:
            break
        time.sleep(INTERVAL_S)
    if PUSH and ok:
        push()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
