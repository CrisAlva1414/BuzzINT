import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_FILE = "manifest.json"
CHUNK_SIZE    = 65_536  # 64 KB

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BuzzINT-Scraper/1.0; "
        "+https://github.com/buzzness-cl)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


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


def verify_manifest(output_dir: Path) -> dict:
    manifest = load_manifest(output_dir)
    counts = {"ok": 0, "corrupt": 0, "missing": 0}

    for key, entry in manifest["files"].items():
        fp = Path(entry.get("filepath", ""))
        if not fp.exists():
            logger.warning("MISSING  %s", entry.get("filename", key))
            counts["missing"] += 1
            continue
        actual = sha256_file(fp)
        if actual == entry.get("hash", ""):
            counts["ok"] += 1
        else:
            logger.warning("CORRUPT  %s", entry.get("filename", key))
            counts["corrupt"] += 1

    logger.info(
        "Verify → OK:%d  Corrupt:%d  Missing:%d",
        counts["ok"], counts["corrupt"], counts["missing"],
    )
    return counts


def safe_filename(name: str, fallback: str, ext: str) -> str:
    name = (name or fallback[:24]).strip()
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "-")
    name = name.strip(". ") or fallback[:24]
    ext  = ext.lstrip(".") or "bin"
    return f"{name}.{ext}"


def already_downloaded(manifest: dict, key: str, filepath: Path) -> bool:
    entry = manifest["files"].get(key, {})
    if entry.get("hash") and filepath.exists():
        if sha256_file(filepath) == entry["hash"]:
            return True
    return False


def write_file_streaming(client, url: str, dest: Path, timeout: int) -> tuple[bool, str]:
    import httpx
    try:
        with client.stream("GET", url, timeout=timeout) as resp:
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=CHUNK_SIZE):
                    fh.write(chunk)
        return True, ""
    except Exception as exc:
        return False, str(exc)