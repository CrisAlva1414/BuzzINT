#!/usr/bin/env python3
import os
import sys
import time
import hashlib
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

import httpx
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

BASE_URL      = "https://sige.mineduc.cl"
ACTAS_URL     = BASE_URL + "/Sige/Reportes/ImprimirActasHisto"
MANIFEST_FILE = "manifest.json"
REQUEST_DELAY = 1.0
TIMEOUT       = 60000  # playwright usa ms

YEARS = list(range(2009, 2026))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(output_dir: Path) -> dict:
    path = output_dir / MANIFEST_FILE
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"created_at": now_iso(), "updated_at": now_iso(), "files": {}}


def save_manifest(output_dir: Path, manifest: dict):
    manifest["updated_at"] = now_iso()
    with open(output_dir / MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def fetch_pdf_links(page, year: int) -> list[str]:
    # el endpoint solo acepta POST — crear form dinámico y submitear
    page.evaluate(f"""() => {{
        const f = document.createElement('form');
        f.method = 'POST';
        f.action = '{ACTAS_URL}';
        const y = document.createElement('input');
        y.name  = 'cmbAnoImpActaH';
        y.value = '{year}';
        f.appendChild(y);
        document.body.appendChild(f);
        f.submit();
    }}""")
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    page.wait_for_selector("#cmbAnoImpActaH", timeout=TIMEOUT)

    links = page.eval_on_selector_all(
        "a[href*='wwwfs.mineduc.cl'][href$='.pdf']",
        "els => els.map(e => e.href)"
    )
    return links


def download_pdf(client: httpx.Client, url: str, year_dir: Path, manifest: dict, dry_run: bool) -> str:
    filename = url.split("/")[-1]
    filepath = year_dir / filename
    key = url

    entry = manifest["files"].get(key, {})
    if entry.get("hash") and filepath.exists():
        if sha256_file(filepath) == entry["hash"]:
            print(f"  skip: {filename}")
            return "skip"

    if dry_run:
        print(f"  [dry] {filename}")
        manifest["files"][key] = {
            "url": url, "filename": filename,
            "discovered_at": now_iso(), "downloaded": False,
        }
        return "ok"

    print(f"  -> {filename}")
    try:
        r = client.get(url, timeout=TIMEOUT / 1000, follow_redirects=True)
        if r.status_code != 200 or len(r.content) < 512:
            print(f"  FAIL {filename} (status={r.status_code})")
            manifest["files"][key] = {
                "url": url, "filename": filename,
                "downloaded": False, "error": f"status_{r.status_code}",
                "discovered_at": now_iso(),
            }
            return "fail"
    except Exception as e:
        print(f"  FAIL {filename}: {e}")
        manifest["files"][key] = {
            "url": url, "filename": filename,
            "downloaded": False, "error": str(e),
            "discovered_at": now_iso(),
        }
        return "fail"

    year_dir.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(r.content)

    file_hash = sha256_file(filepath)
    manifest["files"][key] = {
        "url": url, "filename": filename, "filepath": str(filepath),
        "hash": file_hash, "size_bytes": len(r.content),
        "downloaded": True, "downloaded_at": now_iso(),
    }
    print(f"  ok: {filename} ({len(r.content)//1024} KB) sha256:{file_hash[:12]}")
    return "ok"


def verify_manifest(output_dir: Path):
    manifest = load_manifest(output_dir)
    ok = corrupt = missing = 0
    for key, entry in manifest["files"].items():
        fp = Path(entry.get("filepath", ""))
        if not fp.exists():
            print(f"  MISSING  {entry.get('filename', key)}")
            missing += 1
            continue
        actual = sha256_file(fp)
        stored = entry.get("hash", "")
        if actual == stored:
            print(f"  OK       {entry.get('filename', key)}")
            ok += 1
        else:
            print(f"  CORRUPT  {entry.get('filename', key)}")
            corrupt += 1
    print(f"\nOK:{ok}  Corrupt:{corrupt}  Missing:{missing}")


def browser_login(user: str, pwd: str) -> dict:
    raw_user = user.split("-")
    rut = raw_user[0]
    dv  = raw_user[1] if len(raw_user) > 1 else ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(extra_http_headers=HEADERS)
        page    = context.new_page()

        page.goto(BASE_URL + "/Sige/Login", timeout=TIMEOUT)
        page.wait_for_load_state("networkidle")

        page.fill("#usuario", rut)
        page.fill("#dv", dv)
        page.fill("#clave", pwd)
        page.select_option("#perfil", "1")

        # click directo al botón — el usuario solo resuelve el captcha visual
        page.click("#ingresar-res")

        print(">> Resuelve el captcha y el browser se cerrará automáticamente.")

        # esperar hasta salir de /Login (máx 2 minutos)
        page.wait_for_url(lambda url: "Login" not in url, timeout=120000)

        print(f"login OK -> {page.url}")

        # extraer cookies y cerrar browser — httpx toma el relevo
        cookies = {c["name"]: c["value"] for c in context.cookies()}
        browser.close()

    return cookies


def fetch_pdf_links(client: httpx.Client, year: int) -> list[str]:
    import re
    r = client.post(ACTAS_URL, data={"cmbAnoImpActaH": str(year)}, timeout=TIMEOUT / 1000)
    if r.status_code != 200:
        print(f"  POST {year} -> status {r.status_code}")
        return []
    return re.findall(r'href="(http://wwwfs\.mineduc\.cl/[^"]+\.pdf)"', r.text)


def run(years_filter, output_dir: Path, dry_run: bool):
    user = os.getenv("SIGE_USER", "").strip()
    pwd  = os.getenv("SIGE_PASSWORD", "").strip()
    if not user or not pwd:
        print("ERROR: SIGE_USER y SIGE_PASSWORD requeridos en .env")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(output_dir)
    years = [y for y in YEARS if years_filter is None or y in years_filter]

    # fase 1: login en browser visible → cookies → browser se cierra
    cookies = browser_login(user, pwd)

    # fase 2: httpx con cookies de sesión, sin browser
    client = httpx.Client(headers=HEADERS, cookies=cookies, timeout=TIMEOUT / 1000)
    stats  = {"ok": 0, "skip": 0, "fail": 0, "empty": 0}

    for year in years:
        print(f"[{year}]")
        links = fetch_pdf_links(client, year)

        if not links:
            print(f"  sin PDFs")
            stats["empty"] += 1
            time.sleep(REQUEST_DELAY)
            continue

        print(f"  {len(links)} PDF(s)")
        year_dir = output_dir / str(year)

        for url in links:
            result = download_pdf(client, url, year_dir, manifest, dry_run)
            stats[result] = stats.get(result, 0) + 1
            time.sleep(REQUEST_DELAY)

        if not dry_run:
            save_manifest(output_dir, manifest)

        time.sleep(REQUEST_DELAY)

    client.close()
    save_manifest(output_dir, manifest)
    print(f"\nok:{stats['ok']} skip:{stats['skip']} fail:{stats['fail']} empty:{stats['empty']}")
    print(f"manifest: {(output_dir / MANIFEST_FILE).resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years",   nargs="+", type=int)
    parser.add_argument("--output",  type=Path, default=Path("./data/sige/raw"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify",  action="store_true")
    args = parser.parse_args()

    if args.verify:
        verify_manifest(args.output)
        sys.exit(0)

    run(
        years_filter = set(args.years) if args.years else None,
        output_dir   = args.output,
        dry_run      = args.dry_run,
    )