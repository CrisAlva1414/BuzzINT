import argparse
import logging
import time
from pathlib import Path

import httpx

try:
    from .downloader_base import (
        HEADERS,
        already_downloaded, load_manifest, logger, now_iso,
        safe_filename, save_manifest, sha256_file, verify_manifest,
    )
except ImportError:
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
    from scraper.extractors.downloader_base import (
        HEADERS,
        already_downloaded, load_manifest, logger, now_iso,
        safe_filename, save_manifest, sha256_file, verify_manifest,
    )

BASE_URL      = "https://informacionestadistica.agenciaeducacion.cl"
LIST_URL      = BASE_URL + "/rest/archivo/getAllByCategoriaVistaPublica/{cat_id}/0"
DOWNLOAD_URL  = BASE_URL + "/rest/archivo/obtener?uuid={uuid}"
CAT_ID_MIN    = 2
CAT_ID_MAX    = 120  # Actualizado para incluir SIMCE 2025 (cat 112-114)
REQUEST_DELAY = 0.6
TIMEOUT       = 60


def _fetch_categoria(session: httpx.Client, cat_id: int) -> list:
    try:
        r = session.get(LIST_URL.format(cat_id=cat_id), timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        if isinstance(data, list):
            return data
        # El API a veces envuelve la lista en un dict
        for key in ("data", "archivos", "content", "result"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []
    except Exception as exc:
        logger.debug("[cat %d] error: %s", cat_id, exc)
        return []


def _resolve_download(session: httpx.Client, uuid: str) -> tuple[str | None, bytes | None]:
    url = DOWNLOAD_URL.format(uuid=uuid)
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code == 200 and len(r.content) > 512:
            return url, r.content
    except Exception:
        pass
    return None, None


def _download_archivo(
    session: httpx.Client,
    archivo: dict,
    cat_dir: Path,
    manifest: dict,
    dry_run: bool,
) -> str:
    uuid = str(archivo.get("uuid", "")).strip()
    if not uuid:
        return "fail"

    name = archivo.get("nombre", "") or archivo.get("name", "")
    ext  = (archivo.get("extension", "") or "rar").lower().lstrip(".")
    peso = archivo.get("peso", "") or archivo.get("size", "")
    desc = archivo.get("descripcion", "") or archivo.get("description", "")

    filename = safe_filename(name, uuid, ext)
    filepath = cat_dir / filename

    if already_downloaded(manifest, uuid, filepath):
        logger.debug("skip: %s", filename)
        return "skip"

    manifest["files"][uuid] = {
        "uuid": uuid, "nombre": name, "descripcion": desc,
        "extension": ext, "peso": peso, "filename": filename,
        "discovered_at": now_iso(), "downloaded": False,
    }

    if dry_run:
        logger.info("dry-run  %s  (%s)", name or uuid[:12], peso)
        return "ok"

    logger.info("→ %s  (%s)", name or uuid[:12], peso)
    url_ok, content = _resolve_download(session, uuid)

    if not content:
        manifest["files"][uuid]["error"] = "descarga fallida"
        logger.warning("FAIL  %s", uuid)
        return "fail"

    cat_dir.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(content)

    file_hash = sha256_file(filepath)
    manifest["files"][uuid].update({
        "filepath": str(filepath), "download_url": url_ok,
        "hash": file_hash, "size_bytes": len(content),
        "downloaded": True, "downloaded_at": now_iso(), "error": None,
    })
    logger.info("OK  %s  (%d KB)  sha256:%s", filename, len(content) // 1024, file_hash[:12])
    return "ok"


def run(
    cat_min: int,
    cat_max: int,
    output_dir: Path,
    dry_run: bool,
    only_rar: bool,
) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(output_dir)
    stats    = {"discovered": 0, "ok": 0, "skip": 0, "fail": 0, "empty": 0}

    session = httpx.Client(headers=HEADERS)

    for cat_id in range(cat_min, cat_max + 1):
        archivos = _fetch_categoria(session, cat_id)

        if not archivos:
            logger.debug("[cat %3d] vacío", cat_id)
            stats["empty"] += 1
            time.sleep(REQUEST_DELAY)
            continue

        targets = archivos
        if only_rar:
            targets = [
                a for a in archivos
                if (a.get("extension") or "").lower().strip(".") in ("rar", "")
            ]
            if not targets:
                logger.debug("[cat %3d] %d archivos, sin RAR", cat_id, len(archivos))
                stats["empty"] += 1
                time.sleep(REQUEST_DELAY)
                continue

        logger.info("[cat %3d] %d archivo(s)", cat_id, len(targets))
        stats["discovered"] += len(targets)

        for archivo in targets:
            result = _download_archivo(session, archivo, output_dir, manifest, dry_run)
            stats[result] = stats.get(result, 0) + 1
            time.sleep(REQUEST_DELAY)

        if not dry_run:
            save_manifest(output_dir, manifest)
        time.sleep(REQUEST_DELAY)

    session.close()
    save_manifest(output_dir, manifest)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Descarga datasets SIMCE de la Agencia de Calidad"
    )
    parser.add_argument("--cat-min", type=int, default=CAT_ID_MIN)
    parser.add_argument("--cat-max", type=int, default=CAT_ID_MAX)
    parser.add_argument("--output",  type=Path, default=Path("./data/simce/raw"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all",     action="store_true", help="descargar todos los formatos, no solo RAR")
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
        cat_min   = args.cat_min,
        cat_max   = args.cat_max,
        output_dir = args.output,
        dry_run   = args.dry_run,
        only_rar  = not args.all,
    )
    logger.info(
        "discovered:%d  ok:%d  skip:%d  fail:%d  empty:%d",
        stats["discovered"], stats["ok"], stats["skip"], stats["fail"], stats["empty"],
    )


if __name__ == "__main__":
    main()