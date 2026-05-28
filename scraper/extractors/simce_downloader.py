#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URL      = "https://informacionestadistica.agenciaeducacion.cl"
LIST_URL      = BASE_URL + "/rest/archivo/getAllByCategoriaVistaPublica/{cat_id}/0"
CAT_ID_MIN    = 2
CAT_ID_MAX    = 60
REQUEST_DELAY = 0.6
TIMEOUT       = 60
MANIFEST_FILE = "manifest.json"

# probar en orden hasta que uno retorne contenido válido
DOWNLOAD_PATTERNS = [
    BASE_URL + "/rest/archivo/obtener?uuid={uuid}",
]

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CL,es;q=0.9",
    "Referer": f"{BASE_URL}/",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_categoria(session: httpx.Client, cat_id: int) -> list:
    try:
        r = session.get(LIST_URL.format(cat_id=cat_id), timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "archivos", "content", "result"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []
    except Exception as e:
        print(f"[cat {cat_id}] error: {e}")
        return []


def resolve_download(session: httpx.Client, uuid: str):
    for pattern in DOWNLOAD_PATTERNS:
        url = pattern.format(uuid=uuid)
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 200 and len(r.content) > 512:
                return url, r.content
        except Exception:
            pass
        time.sleep(0.2)
    return None, None


def safe_filename(name: str, uuid: str, ext: str) -> str:
    name = name or uuid[:16]
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "-")
    name = name.strip(". ")
    ext = ext.lstrip(".") or "rar"
    return f"{name}.{ext}"


def download_archivo(session, archivo, cat_dir, manifest, dry_run):
    uuid = str(archivo.get("uuid", "")).strip()
    name = archivo.get("nombre", "") or archivo.get("name", "")
    desc = archivo.get("descripcion", "") or archivo.get("description", "")
    ext  = (archivo.get("extension", "") or "rar").lower().lstrip(".")
    if not ext:
        ext = "rar"
    peso = archivo.get("peso", "") or archivo.get("size", "")

    if not uuid:
        return "fail"

    filename = safe_filename(name, uuid, ext)
    filepath = cat_dir / filename

    # skip si ya está descargado y hash coincide
    entry = manifest["files"].get(uuid, {})
    if entry.get("hash") and filepath.exists():
        if sha256_file(filepath) == entry["hash"]:
            print(f"  skip: {filename}")
            return "skip"
        print(f"  hash mismatch, re-descargando: {filename}")

    if dry_run:
        print(f"  [dry] {name or uuid[:12]} ({peso})")
        manifest["files"][uuid] = {
            "uuid": uuid, "nombre": name, "descripcion": desc,
            "extension": ext, "peso": peso, "filename": filename,
            "discovered_at": now_iso(), "downloaded": False,
        }
        return "ok"

    print(f"  -> {name or uuid[:12]} ({peso})")
    url_ok, content = resolve_download(session, uuid)

    if not content:
        print(f"  FAIL {uuid} — ningún patrón funcionó")
        manifest["files"][uuid] = {
            "uuid": uuid, "nombre": name, "descripcion": desc,
            "extension": ext, "peso": peso, "filename": filename,
            "downloaded": False, "error": "all_patterns_failed",
            "discovered_at": now_iso(),
        }
        return "fail"

    cat_dir.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(content)

    file_hash = sha256_file(filepath)
    manifest["files"][uuid] = {
        "uuid": uuid, "nombre": name, "descripcion": desc,
        "extension": ext, "peso": peso, "filename": filename,
        "filepath": str(filepath), "download_url": url_ok,
        "hash": file_hash, "size_bytes": len(content),
        "downloaded": True, "downloaded_at": now_iso(),
    }
    print(f"  ok: {filename} ({len(content)//1024} KB) sha256:{file_hash[:12]}")
    return "ok"


def verify_manifest(output_dir: Path):
    manifest = load_manifest(output_dir)
    ok = corrupt = missing = 0
    for uuid, entry in manifest["files"].items():
        fp = Path(entry.get("filepath", ""))
        if not fp.exists():
            print(f"  MISSING  {entry.get('filename', uuid)}")
            missing += 1
            continue
        actual = sha256_file(fp)
        stored = entry.get("hash", "")
        if actual == stored:
            print(f"  OK       {entry.get('filename', uuid)}")
            ok += 1
        else:
            print(f"  CORRUPT  {entry.get('filename', uuid)}")
            print(f"           stored={stored[:16]} actual={actual[:16]}")
            corrupt += 1
    print(f"\nOK:{ok}  Corrupt:{corrupt}  Missing:{missing}")


def run(cat_min, cat_max, output_dir, dry_run, only_rar):
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(output_dir)
    session = httpx.Client()
    session.headers.update(HEADERS)

    stats = {"discovered": 0, "ok": 0, "skip": 0, "fail": 0, "empty": 0}

    for cat_id in range(cat_min, cat_max + 1):
        archivos = fetch_categoria(session, cat_id)

        if not archivos:
            print(f"[cat {cat_id:>3}] vacio")
            stats["empty"] += 1
            time.sleep(REQUEST_DELAY)
            continue

        if only_rar:
            # incluir RAR explícito + extensión vacía (el servidor no siempre la declara)
            targets = [
                a for a in archivos
                if (a.get("extension") or "").lower().strip(".") in ("rar", "")
            ]
            if not targets:
                print(f"[cat {cat_id:>3}] {len(archivos)} archivo(s), sin RAR")
                stats["empty"] += 1
                time.sleep(REQUEST_DELAY)
                continue
        else:
            targets = archivos

        print(f"[cat {cat_id:>3}] {len(targets)} archivo(s)")
        stats["discovered"] += len(targets)

        cat_dir = output_dir

        for archivo in targets:
            result = download_archivo(session, archivo, cat_dir, manifest, dry_run)
            stats[result] = stats.get(result, 0) + 1
            time.sleep(REQUEST_DELAY)

        # checkpoint por categoría
        if not dry_run:
            save_manifest(output_dir, manifest)

        time.sleep(REQUEST_DELAY)

    save_manifest(output_dir, manifest)
    print(f"\ndiscovered:{stats['discovered']} ok:{stats['ok']} skip:{stats['skip']} fail:{stats['fail']} empty:{stats['empty']}")
    print(f"manifest: {(output_dir / MANIFEST_FILE).resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cat-min", type=int, default=CAT_ID_MIN)
    parser.add_argument("--cat-max", type=int, default=CAT_ID_MAX)
    parser.add_argument("--output",  type=Path, default=Path("./data/simce/raw"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all",     action="store_true", help="descargar todos los formatos, no solo RAR")
    parser.add_argument("--verify",  action="store_true")
    args = parser.parse_args()

    if args.verify:
        verify_manifest(args.output)
        sys.exit(0)

    run(
        cat_min    = args.cat_min,
        cat_max    = args.cat_max,
        output_dir = args.output,
        dry_run    = args.dry_run,
        only_rar   = not args.all,
    )