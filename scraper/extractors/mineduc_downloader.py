import argparse
import logging
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from .downloader_base import (
    HEADERS, MANIFEST_FILE,
    already_downloaded, load_manifest, logger, now_iso,
    safe_filename, save_manifest, sha256_file, verify_manifest,
    write_file_streaming,
)

REQUEST_DELAY  = 1.0
FETCH_TIMEOUT  = 60
MIN_FILE_BYTES = 512

SOURCES: dict[str, str] = {
    "alumnos": (
        "https://datosabiertos.mineduc.cl"
        "/alumnos-preferentes-prioritarios-y-beneficiarios-sep/"
    ),
    "establecimientos": (
        "https://datosabiertos.mineduc.cl"
        "/directorio-de-establecimientos-educacionales/"
    ),
    "evaluacion": (
        "https://datosabiertos.mineduc.cl/evaluacion-docente/"
    ),
    "cargos": (
        "https://datosabiertos.mineduc.cl/cargos-docentes/"
    ),
}


def _fetch_catalog(source_url: str) -> list[dict]:
    logger.info("Fetching catalog: %s", source_url)
    items: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page    = browser.new_page()
        page.goto(source_url, wait_until="networkidle", timeout=FETCH_TIMEOUT * 1000)

        for link in page.query_selector_all("#accordion .card-link.collapsed"):
            try:
                link.click()
                page.wait_for_timeout(300)
            except Exception:
                pass

        cards = page.query_selector_all("#accordion .card")
        if not cards:
            logger.warning("No se encontraron .card en #accordion — %s", source_url)
            browser.close()
            return []

        for card in cards:
            header    = card.query_selector(".card-header")
            year_text = header.inner_text().strip() if header else "unknown"

            for a_tag in card.query_selector_all(".card-body a[href]"):
                href = (a_tag.get_attribute("href") or "").strip()
                if not href.startswith("http"):
                    continue

                span  = a_tag.query_selector("span")
                title = (
                    a_tag.get_attribute("title")
                    or (span.inner_text().strip() if span else None)
                    or a_tag.inner_text().strip()
                    or ""
                )
                ext      = Path(href.split("?")[0]).suffix.lstrip(".") or "bin"
                filename = safe_filename(title, href.split("/")[-1], ext)
                items.append({"url": href, "filename": filename, "year": year_text, "title": title})

        browser.close()

    logger.info("  → %d archivo(s) descubierto(s)", len(items))
    return items


def _download_item(
    item: dict,
    output_dir: Path,
    manifest: dict,
    client: httpx.Client,
    dry_run: bool,
) -> str:
    url      = item["url"]
    filepath = output_dir / item["filename"]

    if already_downloaded(manifest, url, filepath):
        logger.debug("skip  %s", item["filename"])
        return "skip"

    # Registrar intención antes de descargar (por si falla a mitad)
    manifest["files"][url] = {
        "url": url, "filename": item["filename"],
        "year": item.get("year"), "title": item.get("title"),
        "discovered_at": now_iso(), "downloaded": False,
    }

    if dry_run:
        logger.info("dry-run  %s", item["filename"])
        return "ok"

    ok, err = write_file_streaming(client, url, filepath, FETCH_TIMEOUT)
    if not ok:
        manifest["files"][url]["error"] = err
        logger.warning("FAIL  %s  →  %s", item["filename"], err)
        return "fail"

    size = filepath.stat().st_size
    if size < MIN_FILE_BYTES:
        manifest["files"][url]["error"] = f"respuesta demasiado pequeña ({size} bytes)"
        filepath.unlink(missing_ok=True)
        logger.warning("FAIL  %s  →  tamaño sospechoso %d bytes", item["filename"], size)
        return "fail"

    file_hash = sha256_file(filepath)
    manifest["files"][url].update({
        "filepath": str(filepath.resolve()),
        "hash": file_hash, "size_bytes": size,
        "downloaded": True, "downloaded_at": now_iso(), "error": None,
    })
    logger.info("OK    %s  (%d bytes)", item["filename"], size)
    return "ok"


def run_source(
    key: str,
    source_url: str,
    base_output: Path,
    dry_run: bool,
) -> dict[str, int]:
    output_dir = base_output / key
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(output_dir)
    stats: dict[str, int] = {"ok": 0, "skip": 0, "fail": 0, "empty": 0}

    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        items = _fetch_catalog(source_url)

        if not items:
            stats["empty"] = 1
            logger.warning("[%s] Catálogo vacío.", key)
            save_manifest(output_dir, manifest)
            return stats

        for i, item in enumerate(items, start=1):
            logger.info("[%s] (%d/%d) %s", key, i, len(items), item["filename"])
            result = _download_item(item, output_dir, manifest, client, dry_run)
            stats[result] = stats.get(result, 0) + 1

            if i % 10 == 0:
                save_manifest(output_dir, manifest)

            if result == "ok" and not dry_run:
                time.sleep(REQUEST_DELAY)

    save_manifest(output_dir, manifest)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Descarga datasets de datosabiertos.mineduc.cl"
    )
    parser.add_argument("--output",  type=Path, default=Path("./data/mineduc/raw"))
    parser.add_argument("--sources", nargs="+", choices=list(SOURCES.keys()), default=list(SOURCES.keys()))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify",  action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.verify:
        for key in args.sources:
            logger.info("=== Verificando: %s ===", key)
            verify_manifest(args.output / key)
        return

    total: dict[str, int] = {"ok": 0, "skip": 0, "fail": 0, "empty": 0}

    for key in args.sources:
        logger.info("=" * 60)
        logger.info("Fuente: %s", key)
        stats = run_source(key, SOURCES[key], args.output, args.dry_run)
        for k, v in stats.items():
            total[k] += v
        logger.info("[%s] ok=%d  skip=%d  fail=%d  empty=%d", key, *stats.values())

    logger.info("TOTAL  ok=%d  skip=%d  fail=%d  empty=%d", *total.values())


if __name__ == "__main__":
    main()