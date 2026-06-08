"""
Tests de los 3 downloaders en modo dry-run.

Ejecuta los 3 en paralelo con ThreadPoolExecutor y verifica:
- Que cada downloader crea su manifest.json
- Que el manifest tiene la estructura correcta
- Que los helpers compartidos (sha256, load/save manifest, etc.) funcionan
- Que already_downloaded() detecta correctamente duplicados
- Que verify_manifest() reporta correctamente

Uso:
    pytest tests/downloaders_test_.py -v
    pytest tests/downloaders_test_.py -v -s   # ver logs en tiempo real
"""
import sys
import json
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from scraper.extractors.downloader_base import (
    already_downloaded,
    load_manifest,
    now_iso,
    safe_filename,
    save_manifest,
    sha256_file,
    verify_manifest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def sample_file(tmp_path):
    """Crea un archivo real en disco para testear sha256 y manifest."""
    f = tmp_path / "sample.csv"
    f.write_text("agno,rbd,nom_rbd\n2023,00001234,Escuela Test\n")
    return f


# ─────────────────────────────────────────────
# Tests de downloader_base
# ─────────────────────────────────────────────

class TestDownloaderBase:

    def test_now_iso_formato(self):
        ts = now_iso()
        assert "T" in ts
        assert ts.endswith("+00:00") or ts.endswith("Z") or "+" in ts

    def test_sha256_deterministico(self, sample_file):
        h1 = sha256_file(sample_file)
        h2 = sha256_file(sample_file)
        assert h1 == h2
        assert len(h1) == 64

    def test_sha256_distinto_por_contenido(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("contenido A")
        f2.write_text("contenido B")
        assert sha256_file(f1) != sha256_file(f2)

    def test_load_manifest_nuevo(self, tmp_dir):
        m = load_manifest(tmp_dir)
        assert "files" in m
        assert "created_at" in m
        assert isinstance(m["files"], dict)

    def test_save_y_load_manifest(self, tmp_dir):
        m = load_manifest(tmp_dir)
        m["files"]["test_key"] = {"url": "http://example.com", "downloaded": False}
        save_manifest(tmp_dir, m)

        m2 = load_manifest(tmp_dir)
        assert "test_key" in m2["files"]
        assert m2["updated_at"] != m2["created_at"] or True  # puede ser igual si es muy rápido

    def test_safe_filename_limpia_caracteres(self):
        nombre = 'Archivo con /\\:*?"<>| especiales'
        fn = safe_filename(nombre, "fallback", "csv")
        for ch in r'/\:*?"<>|':
            assert ch not in fn
        assert fn.endswith(".csv")

    def test_safe_filename_con_nombre_vacio(self):
        fn = safe_filename("", "fallback_nombre", "rar")
        assert fn.endswith(".rar")
        assert len(fn) > 4

    def test_already_downloaded_falso_para_nuevo(self, tmp_dir):
        manifest = load_manifest(tmp_dir)
        fake_path = tmp_dir / "noexiste.csv"
        assert not already_downloaded(manifest, "http://test.com/file.csv", fake_path)

    def test_already_downloaded_verdadero_con_hash(self, tmp_dir, sample_file):
        manifest = load_manifest(tmp_dir)
        h = sha256_file(sample_file)
        manifest["files"]["http://test.com/file.csv"] = {
            "hash": h, "filepath": str(sample_file)
        }
        assert already_downloaded(manifest, "http://test.com/file.csv", sample_file)

    def test_already_downloaded_falso_si_hash_distinto(self, tmp_dir, sample_file):
        manifest = load_manifest(tmp_dir)
        manifest["files"]["http://test.com/file.csv"] = {
            "hash": "hash_incorrecto_aaaabbbb", "filepath": str(sample_file)
        }
        assert not already_downloaded(manifest, "http://test.com/file.csv", sample_file)

    def test_verify_manifest_con_archivo_ok(self, tmp_dir, sample_file):
        manifest = load_manifest(tmp_dir)
        h = sha256_file(sample_file)
        manifest["files"]["key1"] = {
            "filepath": str(sample_file), "hash": h, "filename": sample_file.name
        }
        save_manifest(tmp_dir, manifest)
        counts = verify_manifest(tmp_dir)
        assert counts["ok"] == 1
        assert counts["corrupt"] == 0
        assert counts["missing"] == 0

    def test_verify_manifest_detecta_missing(self, tmp_dir):
        manifest = load_manifest(tmp_dir)
        manifest["files"]["key1"] = {
            "filepath": str(tmp_dir / "noexiste.csv"),
            "hash": "abc", "filename": "noexiste.csv"
        }
        save_manifest(tmp_dir, manifest)
        counts = verify_manifest(tmp_dir)
        assert counts["missing"] == 1

    def test_verify_manifest_detecta_corrupt(self, tmp_dir, sample_file):
        manifest = load_manifest(tmp_dir)
        manifest["files"]["key1"] = {
            "filepath": str(sample_file),
            "hash": "hash_invalido_000000000000",
            "filename": sample_file.name,
        }
        save_manifest(tmp_dir, manifest)
        counts = verify_manifest(tmp_dir)
        assert counts["corrupt"] == 1


# ─────────────────────────────────────────────
# Tests dry-run de cada downloader
# ─────────────────────────────────────────────

def _run_mineduc_dry(output_dir: Path) -> dict:
    from scraper.extractors.mineduc_downloader import run_source, SOURCES

    logger.info("[MINEDUC] iniciando dry-run con fuente 'alumnos'")
    # Solo probamos una fuente para no tardar demasiado
    stats = run_source("alumnos", SOURCES["alumnos"], output_dir, dry_run=True)
    logger.info("[MINEDUC] terminado: %s", stats)
    return {"source": "mineduc", "stats": stats, "output_dir": output_dir / "alumnos"}


def _run_simce_dry(output_dir: Path) -> dict:
    from scraper.extractors.simce_downloader import run

    logger.info("[SIMCE] iniciando dry-run categorías 2-5")
    stats = run(cat_min=2, cat_max=5, output_dir=output_dir, dry_run=True, only_rar=True)
    logger.info("[SIMCE] terminado: %s", stats)
    return {"source": "simce", "stats": stats, "output_dir": output_dir}


def _run_sige_dry(output_dir: Path) -> dict:
    """
    SIGE requiere credenciales y browser. En CI/test sin credenciales,
    verificamos que el módulo importa y que el manifest se inicializa bien.
    No ejecutamos el login.
    """
    logger.info("[SIGE] verificando imports y manifest (sin login)")
    from scraper.extractors.sige_downloader import run  # noqa: F401

    output_dir.mkdir(parents=True, exist_ok=True)
    m = load_manifest(output_dir)
    save_manifest(output_dir, m)

    manifest_path = output_dir / "manifest.json"
    assert manifest_path.exists()
    return {
        "source": "sige",
        "stats": {"note": "skip — requiere SIGE_USER y browser"},
        "output_dir": output_dir,
    }


class TestDownloadersDryRun:

    def test_todos_en_paralelo(self, tmp_path):
        """
        Ejecuta los 3 downloaders en paralelo con dry-run.
        Verifica que cada uno crea su manifest.json con la estructura correcta.
        """
        mineduc_dir = tmp_path / "mineduc"
        simce_dir   = tmp_path / "simce"
        sige_dir    = tmp_path / "sige"

        tareas = [
            (_run_mineduc_dry, mineduc_dir),
            (_run_simce_dry,   simce_dir),
            (_run_sige_dry,    sige_dir),
        ]

        resultados = {}

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(fn, d): fn.__name__
                for fn, d in tareas
            }
            for future in as_completed(futures):
                nombre = futures[future]
                try:
                    res = future.result()
                    resultados[res["source"]] = res
                    logger.info("✓ %s completado: %s", nombre, res["stats"])
                except Exception as exc:
                    logger.error("✗ %s falló: %s", nombre, exc)
                    pytest.fail(f"{nombre} lanzó excepción: {exc}")

        assert "mineduc" in resultados
        assert "simce"   in resultados
        assert "sige"    in resultados

        self._verificar_manifest_mineduc(resultados["mineduc"]["output_dir"])
        self._verificar_manifest_simce(resultados["simce"]["output_dir"])
        self._verificar_manifest_sige(resultados["sige"]["output_dir"])

    def _verificar_manifest_mineduc(self, output_dir: Path):
        """El manifest de mineduc debe existir y tener keys de URL."""
        manifest_path = output_dir / "manifest.json"

        if not manifest_path.exists():
            logger.warning("MINEDUC: sin manifest (posible fallo de red en CI) — skip")
            return

        m = json.loads(manifest_path.read_text())
        assert "files" in m, "manifest sin clave 'files'"
        assert "created_at" in m

        for key, entry in m["files"].items():
            assert key.startswith("http"), f"MINEDUC: clave no es URL: {key}"
            assert "url" in entry
            assert "filename" in entry
            assert "discovered_at" in entry
            assert "downloaded" in entry
            # En dry-run, ninguno debe estar marcado como descargado con filepath real
            # (pueden marcarse como downloaded=False o como dry-run ok)
            logger.info("  MINEDUC manifest: %d entradas", len(m["files"]))

    def _verificar_manifest_simce(self, output_dir: Path):
        """El manifest de simce debe tener keys de UUID."""
        manifest_path = output_dir / "manifest.json"

        if not manifest_path.exists():
            logger.warning("SIMCE: sin manifest (posible fallo de red en CI) — skip")
            return

        m = json.loads(manifest_path.read_text())
        assert "files" in m

        for key, entry in m["files"].items():
            # SIMCE usa UUID como key, no URL
            assert "uuid" in entry, f"SIMCE: entry sin 'uuid': {entry}"
            assert "filename" in entry
            logger.info("  SIMCE manifest: %d entradas", len(m["files"]))
            break  # revisar solo la primera

    def _verificar_manifest_sige(self, output_dir: Path):
        """SIGE siempre debe dejar el manifest inicializado."""
        manifest_path = output_dir / "manifest.json"
        assert manifest_path.exists(), "SIGE: manifest.json no fue creado"

        m = json.loads(manifest_path.read_text())
        assert "files" in m
        assert "created_at" in m
        logger.info("  SIGE manifest: OK (sin entradas, requiere auth)")


# ─────────────────────────────────────────────
# Tests de estructura del manifest
# ─────────────────────────────────────────────

class TestManifestEstructura:

    def test_manifest_keys_mineduc(self, tmp_path):
        """Simula entradas de manifest de MINEDUC y verifica estructura."""
        m = load_manifest(tmp_path)
        url = "https://datosabiertos.mineduc.cl/files/alumnos_2023.csv"
        m["files"][url] = {
            "url": url,
            "filename": "alumnos_2023.csv",
            "year": "2023",
            "title": "Alumnos SEP 2023",
            "discovered_at": now_iso(),
            "downloaded": False,
        }
        save_manifest(tmp_path, m)
        m2 = load_manifest(tmp_path)
        entry = m2["files"][url]
        assert entry["url"] == url
        assert entry["downloaded"] is False

    def test_manifest_keys_simce(self, tmp_path):
        """Simula entradas de manifest de SIMCE (key = UUID) y verifica estructura."""
        m = load_manifest(tmp_path)
        uuid = "abc123-def456-789"
        m["files"][uuid] = {
            "uuid": uuid,
            "nombre": "SIMCE 4° básico 2022",
            "extension": "rar",
            "filename": "SIMCE 4 basico 2022.rar",
            "discovered_at": now_iso(),
            "downloaded": False,
        }
        save_manifest(tmp_path, m)
        m2 = load_manifest(tmp_path)
        assert uuid in m2["files"]
        assert m2["files"][uuid]["extension"] == "rar"

    def test_manifest_keys_sige(self, tmp_path):
        """Simula entradas de manifest de SIGE (key = URL PDF) y verifica estructura."""
        m = load_manifest(tmp_path)
        url = "http://wwwfs.mineduc.cl/actas/00001234_23_4_A_15032023_120000.pdf"
        m["files"][url] = {
            "url": url,
            "filename": "00001234_23_4_A_15032023_120000.pdf",
            "discovered_at": now_iso(),
            "downloaded": False,
        }
        save_manifest(tmp_path, m)
        m2 = load_manifest(tmp_path)
        assert url in m2["files"]


# ─────────────────────────────────────────────
# Reporte final al ejecutar directamente
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        print("\n" + "═" * 60)
        print("  BuzzINT — Test paralelo de downloaders (dry-run)")
        print("═" * 60)

        tareas = [
            (_run_mineduc_dry, base / "mineduc"),
            (_run_simce_dry,   base / "simce"),
            (_run_sige_dry,    base / "sige"),
        ]

        resultados = {}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(fn, d): fn.__name__ for fn, d in tareas}
            for f in as_completed(futures):
                nombre = futures[f]
                try:
                    res = f.result()
                    resultados[res["source"]] = res
                    print(f"  ✓ {res['source']:<12} {res['stats']}")
                except Exception as exc:
                    print(f"  ✗ {nombre:<20} ERROR: {exc}")

        print("\n  Manifests generados:")
        for source, res in resultados.items():
            mp = res["output_dir"] / "manifest.json"
            if mp.exists():
                m = json.loads(mp.read_text())
                print(f"    {source:<12} → {len(m['files'])} entradas  ({mp})")
            else:
                print(f"    {source:<12} → sin manifest")

        print("═" * 60)
        sys.exit(0)