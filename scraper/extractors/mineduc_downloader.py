"""
datos_abiertos_downloader.py
Scraper Tipo A — HTML estático sin autenticación
Fuente: datosabiertos.mineduc.cl

Descarga archivos de 4 secciones del sitio hacia:
    data/mineduc/raw/alumnos/
    data/mineduc/raw/establecimientos/
    data/mineduc/raw/evaluacion/
    data/mineduc/raw/cargos/

Uso:
    python datos_abiertos_downloader.py [--output PATH] [--dry-run] [--verify]
    python datos_abiertos_downloader.py --sources alumnos establecimientos
"""

import argparse
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

REQUEST_DELAY: float = 1.0          # segundos entre requests de descarga
FETCH_TIMEOUT: int   = 60           # timeout httpx (segundos)
MIN_FILE_BYTES: int  = 512          # archivos menores se tratan como error
MANIFEST_FILE: str   = "manifest.json"
CHUNK_SIZE: int      = 65536        # 64 KB para lectura en streaming

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Funciones base (patrón uniforme del proyecto)
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(output_dir: Path) -> dict:
    path = output_dir / MANIFEST_FILE
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"created_at": now_iso(), "updated_at": now_iso(), "files": {}}


def save_manifest(output_dir: Path, manifest: dict) -> None:
    manifest["updated_at"] = now_iso()
    with open(output_dir / MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def verify_manifest(output_dir: Path) -> None:
    manifest = load_manifest(output_dir)
    ok = corrupt = missing = 0
    for key, entry in manifest["files"].items():
        fp = Path(entry.get("filepath", ""))
        if not fp.exists():
            logger.warning("MISSING  %s", fp)
            missing += 1
            continue
        actual = sha256_file(fp)
        if actual == entry.get("hash", ""):
            ok += 1
        else:
            logger.warning("CORRUPT  %s", fp)
            corrupt += 1
    logger.info("Verify → OK:%d  Corrupt:%d  Missing:%d", ok, corrupt, missing)


def safe_filename(name: str, fallback: str, ext: str) -> str:
    name = (name or fallback[:24]).strip()
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "-")
    name = name.strip(". ") or fallback[:24]
    ext  = ext.lstrip(".") or "bin"
    return f"{name}.{ext}"


# ---------------------------------------------------------------------------
# Catálogo: descubre todos los enlaces dentro del acordeón
# ---------------------------------------------------------------------------

def fetch_catalog(source_url: str) -> list[dict]:
    """
    Abre la página con Playwright, expande todos los paneles del acordeón
    y extrae los href de descarga.
    Retorna lista de dicts: {url, filename, year, title}
    """
    logger.info("Fetching catalog: %s", source_url)
    items: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(source_url, wait_until="networkidle", timeout=FETCH_TIMEOUT * 1000)

        # Expandir todos los paneles colapsados del acordeón
        collapsed = page.query_selector_all("#accordion .card-link.collapsed")
        for link in collapsed:
            try:
                link.click()
                page.wait_for_timeout(300)
            except Exception:
                pass

        # Iterar por cada .card
        cards = page.query_selector_all("#accordion .card")
        if not cards:
            logger.warning("No se encontraron .card en #accordion — %s", source_url)
            browser.close()
            return []

        for card in cards:
            # Año desde el encabezado
            header = card.query_selector(".card-header")
            year_text = (header.inner_text().strip() if header else "unknown")

            # Enlaces de descarga dentro del card-body
            links = card.query_selector_all(".card-body a[href]")
            for a_tag in links:
                href: str = (a_tag.get_attribute("href") or "").strip()
                if not href.startswith("http"):
                    continue

                title: str = (
                    a_tag.get_attribute("title")
                    or a_tag.query_selector("span")
                    and a_tag.query_selector("span").inner_text().strip()
                    or a_tag.inner_text().strip()
                    or ""
                )

                ext = Path(href.split("?")[0]).suffix.lstrip(".") or "bin"
                filename = safe_filename(title, href.split("/")[-1], ext)

                items.append(
                    {
                        "url": href,
                        "filename": filename,
                        "year": year_text,
                        "title": title,
                    }
                )

        browser.close()

    logger.info("  → %d archivo(s) descubierto(s)", len(items))
    return items


# ---------------------------------------------------------------------------
# Descarga individual
# ---------------------------------------------------------------------------

def download_item(
    item: dict,
    output_dir: Path,
    manifest: dict,
    client: httpx.Client,
    dry_run: bool,
) -> str:
    """
    Descarga un archivo y actualiza el manifest.
    Retorna: "ok" | "skip" | "fail"
    """
    url: str      = item["url"]
    filename: str = item["filename"]
    filepath: Path = output_dir / filename

    # --- Deduplicación ---
    entry = manifest["files"].get(url, {})
    if entry.get("hash") and filepath.exists():
        if sha256_file(filepath) == entry["hash"]:
            logger.debug("  skip  %s", filename)
            return "skip"
        logger.info("  hash mismatch → re-descargando  %s", filename)

    # --- Registro pre-descarga (dry-run o punto de partida en caso de fallo) ---
    manifest["files"][url] = {
        "url": url,
        "filename": filename,
        "year": item.get("year"),
        "title": item.get("title"),
        "discovered_at": now_iso(),
        "downloaded": False,
    }

    if dry_run:
        logger.info("  dry-run  %s", filename)
        return "ok"

    # --- Descarga en streaming ---
    try:
        with client.stream("GET", url, timeout=FETCH_TIMEOUT) as resp:
            resp.raise_for_status()
            output_dir.mkdir(parents=True, exist_ok=True)
            with open(filepath, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=CHUNK_SIZE):
                    fh.write(chunk)
    except Exception as exc:
        manifest["files"][url]["error"] = str(exc)
        logger.warning("  FAIL  %s  →  %s", filename, exc)
        return "fail"

    # --- Validar tamaño mínimo ---
    size = filepath.stat().st_size
    if size < MIN_FILE_BYTES:
        manifest["files"][url]["error"] = f"respuesta demasiado pequeña ({size} bytes)"
        filepath.unlink(missing_ok=True)
        logger.warning("  FAIL  %s  →  tamaño sospechoso %d bytes", filename, size)
        return "fail"

    # --- Registro post-descarga exitoso ---
    file_hash = sha256_file(filepath)
    manifest["files"][url].update(
        {
            "filepath": str(filepath.resolve()),
            "hash": file_hash,
            "size_bytes": size,
            "downloaded": True,
            "downloaded_at": now_iso(),
            "error": None,
        }
    )
    logger.info("  OK    %s  (%d bytes)", filename, size)
    return "ok"


# ---------------------------------------------------------------------------
# Runner por fuente
# ---------------------------------------------------------------------------

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

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; BuzzINT-Scraper/1.0; "
            "+https://github.com/buzzness-cl)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        items = fetch_catalog(source_url)

        if not items:
            stats["empty"] = 1
            logger.warning("[%s] Catálogo vacío.", key)
            save_manifest(output_dir, manifest)
            return stats

        for i, item in enumerate(items, start=1):
            logger.info("[%s] (%d/%d) %s", key, i, len(items), item["filename"])
            result = download_item(item, output_dir, manifest, client, dry_run)
            stats[result] = stats.get(result, 0) + 1

            # Checkpoint cada 10 archivos
            if i % 10 == 0:
                save_manifest(output_dir, manifest)

            if result == "ok" and not dry_run:
                time.sleep(REQUEST_DELAY)

    save_manifest(output_dir, manifest)
    return stats


# ---------------------------------------------------------------------------
# Entrypoint CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Descarga datasets de datosabiertos.mineduc.cl"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./data/mineduc/raw"),
        help="Directorio base de salida (default: ./data/mineduc/raw)",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=list(SOURCES.keys()),
        default=list(SOURCES.keys()),
        help="Fuentes a descargar (default: todas)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Registra en manifest pero no descarga archivos",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verifica hashes en disco contra manifest; no descarga",
    )
    args = parser.parse_args()

    if args.verify:
        for key in args.sources:
            output_dir = args.output / key
            logger.info("=== Verificando: %s ===", key)
            verify_manifest(output_dir)
        return

    total_stats: dict[str, int] = {"ok": 0, "skip": 0, "fail": 0, "empty": 0}

    for key in args.sources:
        logger.info("=" * 60)
        logger.info("Fuente: %s", key)
        logger.info("=" * 60)
        source_url = SOURCES[key]
        stats = run_source(key, source_url, args.output, args.dry_run)
        for k, v in stats.items():
            total_stats[k] += v
        logger.info(
            "[%s] ok=%d  skip=%d  fail=%d  empty=%d",
            key, stats["ok"], stats["skip"], stats["fail"], stats["empty"],
        )

    logger.info("=" * 60)
    logger.info(
        "TOTAL  ok=%d  skip=%d  fail=%d  empty=%d",
        total_stats["ok"],
        total_stats["skip"],
        total_stats["fail"],
        total_stats["empty"],
    )


if __name__ == "__main__":
    main()