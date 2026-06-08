import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

try:
    from .downloader_base import (
        HEADERS,
        already_downloaded, load_manifest, logger, now_iso,
        save_manifest, sha256_file, verify_manifest,
    )
except ImportError:
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
    from scraper.extractors.downloader_base import (
        HEADERS,
        already_downloaded, load_manifest, logger, now_iso,
        save_manifest, sha256_file, verify_manifest,
    )

load_dotenv()

BASE_URL      = "https://sige.mineduc.cl"
ACTAS_URL     = BASE_URL + "/Sige/Reportes/ImprimirActasHisto"
REQUEST_DELAY = 1.0
TIMEOUT_MS    = 60_000   # para Playwright (milisegundos)
TIMEOUT_S     = 60       # para httpx (segundos)
YEARS         = list(range(2009, 2026))


def _browser_login(rut: str, dv: str, password: str) -> dict[str, str]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(extra_http_headers=HEADERS)
        page    = context.new_page()

        page.goto(BASE_URL + "/Sige/Login", timeout=TIMEOUT_MS)
        page.wait_for_load_state("networkidle")

        page.fill("#usuario", rut)
        page.fill("#dv",      dv)
        page.fill("#clave",   password)
        page.select_option("#perfil", "1")
        page.click("#ingresar-res")

        print("\n>> Resuelve el CAPTCHA en el browser. Se cerrará automáticamente al ingresar.")

        # Espera hasta 2 minutos a que la URL cambie (login exitoso)
        page.wait_for_url(lambda url: "Login" not in url, timeout=120_000)
        logger.info("Login OK → %s", page.url)

        cookies = {c["name"]: c["value"] for c in context.cookies()}
        browser.close()

    return cookies


def _fetch_pdf_links(client: httpx.Client, year: int) -> list[str]:
    r = client.post(ACTAS_URL, data={"cmbAnoImpActaH": str(year)}, timeout=TIMEOUT_S)
    if r.status_code != 200:
        logger.warning("POST %d → status %d", year, r.status_code)
        return []
    return re.findall(r'href="(http://wwwfs\.mineduc\.cl/[^"]+\.pdf)"', r.text)


def _download_pdf(
    client: httpx.Client,
    url: str,
    year_dir: Path,
    manifest: dict,
    dry_run: bool,
) -> str:
    filename = url.split("/")[-1]
    filepath = year_dir / filename

    if already_downloaded(manifest, url, filepath):
        logger.debug("skip: %s", filename)
        return "skip"

    manifest["files"][url] = {
        "url": url, "filename": filename,
        "discovered_at": now_iso(), "downloaded": False,
    }

    if dry_run:
        logger.info("dry-run  %s", filename)
        return "ok"

    logger.info("→ %s", filename)
    try:
        r = client.get(url, timeout=TIMEOUT_S, follow_redirects=True)
        if r.status_code != 200 or len(r.content) < 512:
            manifest["files"][url]["error"] = f"status_{r.status_code}"
            logger.warning("FAIL  %s  (status=%d)", filename, r.status_code)
            return "fail"
    except Exception as exc:
        manifest["files"][url]["error"] = str(exc)
        logger.warning("FAIL  %s  → %s", filename, exc)
        return "fail"

    year_dir.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(r.content)

    file_hash = sha256_file(filepath)
    manifest["files"][url].update({
        "filepath": str(filepath), "hash": file_hash,
        "size_bytes": len(r.content),
        "downloaded": True, "downloaded_at": now_iso(), "error": None,
    })
    logger.info("OK  %s  (%d KB)  sha256:%s", filename, len(r.content) // 1024, file_hash[:12])
    return "ok"


def run(
    years_filter: set[int] | None,
    output_dir: Path,
    dry_run: bool,
) -> dict[str, int]:
    user = os.getenv("SIGE_USER", "").strip()
    pwd  = os.getenv("SIGE_PASSWORD", "").strip()
    if not user or not pwd:
        logger.error("SIGE_USER y SIGE_PASSWORD son requeridos en .env")
        sys.exit(1)

    # Separar rut y dv: "12345678-9" → rut="12345678", dv="9"
    parts = user.split("-")
    rut, dv = parts[0], (parts[1] if len(parts) > 1 else "")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(output_dir)
    years    = [y for y in YEARS if years_filter is None or y in years_filter]
    stats    = {"ok": 0, "skip": 0, "fail": 0, "empty": 0}

    cookies = _browser_login(rut, dv, pwd)

    with httpx.Client(headers=HEADERS, cookies=cookies) as client:
        for year in years:
            logger.info("[%d] buscando PDFs...", year)
            links = _fetch_pdf_links(client, year)

            if not links:
                logger.info("[%d] sin PDFs", year)
                stats["empty"] += 1
                time.sleep(REQUEST_DELAY)
                continue

            logger.info("[%d] %d PDF(s)", year, len(links))
            year_dir = output_dir / str(year)

            for url in links:
                result = _download_pdf(client, url, year_dir, manifest, dry_run)
                stats[result] = stats.get(result, 0) + 1
                time.sleep(REQUEST_DELAY)

            if not dry_run:
                save_manifest(output_dir, manifest)
            time.sleep(REQUEST_DELAY)

    save_manifest(output_dir, manifest)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Descarga actas PDF del SIGE"
    )
    parser.add_argument("--years",   nargs="+", type=int)
    parser.add_argument("--output",  type=Path, default=Path("./data/sige/raw"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify",  action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.verify:
        verify_manifest(args.output)
        return

    stats = run(
        years_filter = set(args.years) if args.years else None,
        output_dir   = args.output,
        dry_run      = args.dry_run,
    )
    logger.info(
        "ok:%d  skip:%d  fail:%d  empty:%d",
        stats["ok"], stats["skip"], stats["fail"], stats["empty"],
    )


if __name__ == "__main__":
    main()