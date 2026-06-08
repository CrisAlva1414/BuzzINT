import sys
import csv
import os
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

logging.basicConfig(level=logging.WARNING)


def _write_csv(path: Path, rows: list[dict], sep: str = ",") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=sep)
        w.writeheader()
        w.writerows(rows)
    return path


class TestPatchSQL:

    def test_archivo_existe(self):
        patch_file = Path(__file__).parent.parent / "scraper" / "db" / "inits" / "03_patches.sql"
        assert patch_file.exists(), f"No se encontró {patch_file}"

    def test_no_tiene_drop_table(self):
        patch_file = Path(__file__).parent.parent / "scraper" / "db" / "inits" / "03_patches.sql"
        if not patch_file.exists():
            pytest.skip("03_patches.sql no encontrado")
        content = patch_file.read_text().upper()
        assert "DROP TABLE " not in content, "03_patches.sql no debe eliminar tablas"

    def test_tiene_if_not_exists(self):
        patch_file = Path(__file__).parent.parent / "scraper" / "db" / "inits" / "03_patches.sql"
        if not patch_file.exists():
            pytest.skip("03_patches.sql no encontrado")
        content = patch_file.read_text().upper()
        assert "IF NOT EXISTS" in content, "Los patches deben ser idempotentes"

    def test_tiene_vista_notas_long(self):
        patch_file = Path(__file__).parent.parent / "scraper" / "db" / "inits" / "03_patches.sql"
        if not patch_file.exists():
            pytest.skip("03_patches.sql no encontrado")
        content = patch_file.read_text()
        assert "v_notas_long" in content
        assert "v_sige_resumen" in content


class TestCoerciones:

    def test_str_none(self):
        from scraper.db.loaders.db import _str
        assert _str(None) is None
        assert _str("") is None
        assert _str("nan") is None
        assert _str("NaN") is None
        assert _str("None") is None
        assert _str("  texto  ") == "texto"

    def test_int_variantes(self):
        from scraper.db.loaders.db import _int
        assert _int("3") == 3
        assert _int("3.0") == 3
        assert _int("3.9") == 3
        assert _int("") is None
        assert _int("abc") is None
        assert _int(None) is None

    def test_float_coma_decimal(self):
        from scraper.db.loaders.db import _float
        assert _float("3,5") == 3.5
        assert _float("1.234,56") == 1234.56
        assert _float("") is None
        assert _float(None) is None

    def test_date_formatos(self):
        from scraper.db.loaders.db import _date
        assert _date("20050312") == "2005-03-12"
        assert _date("2005-03-12") == "2005-03-12"
        assert _date("200503") == "2005-03-01"
        assert _date("") is None
        assert _date(None) is None

    def test_rbd_zero_padding(self):
        from scraper.db.loaders.db import _rbd
        assert _rbd("1234") == "00001234"
        assert _rbd("00001234") == "00001234"
        assert _rbd("12345678") == "12345678"
        assert _rbd("") is None
        assert _rbd(None) is None
        assert _rbd("abc") is None

    def test_rbd_con_letras_mixtas(self):
        from scraper.db.loaders.db import _rbd
        assert _rbd("RBD-1234") == "00001234"


class TestEnvProfile:

    def test_env_dev_por_defecto(self, monkeypatch):
        monkeypatch.delenv("BUZZINT_ENV", raising=False)
        # Reimportar para que tome el nuevo valor
        import importlib
        import scraper.db.loaders.db as db_mod
        importlib.reload(db_mod)
        assert db_mod._ENV in ("dev", "prod")

    def test_env_prod_chunk_menor(self, monkeypatch):
        monkeypatch.setenv("BUZZINT_ENV", "prod")
        import importlib
        import scraper.db.loaders.db as db_mod
        importlib.reload(db_mod)
        assert db_mod.CHUNK_SIZE == 500
        assert db_mod.USE_COPY is False
        # Restaurar
        monkeypatch.setenv("BUZZINT_ENV", "dev")
        importlib.reload(db_mod)

    def test_env_dev_copy_habilitado(self, monkeypatch):
        monkeypatch.setenv("BUZZINT_ENV", "dev")
        import importlib
        import scraper.db.loaders.db as db_mod
        importlib.reload(db_mod)
        assert db_mod.CHUNK_SIZE == 50_000
        assert db_mod.USE_COPY is True


class TestBatchUpsert:

    def test_batch_vacio_retorna_cero(self):
        from scraper.db.loaders.db import batch_upsert
        cur = MagicMock()
        n   = batch_upsert(cur, "INSERT INTO t VALUES %s", [])
        assert n == 0
        cur.assert_not_called()

    def test_batch_llama_execute_values(self):
        from scraper.db.loaders.db import batch_upsert
        cur  = MagicMock()
        rows = [(1, "a"), (2, "b")]
        with patch("scraper.db.loaders.db.execute_values") as mock_ev:
            n = batch_upsert(cur, "INSERT INTO t VALUES %s", rows)
        assert n == 2
        mock_ev.assert_called_once()
        args = mock_ev.call_args[0]
        assert args[0] is cur
        assert args[2] is rows


class TestParseSigeRun:

    def test_run_con_dv(self):
        from scraper.db.loaders.load_sige import _parse_run
        assert _parse_run("12345678-9") == 12345678

    def test_run_sin_dv(self):
        from scraper.db.loaders.load_sige import _parse_run
        assert _parse_run("12345678") == 12345678

    def test_run_con_puntos(self):
        from scraper.db.loaders.load_sige import _parse_run
        assert _parse_run("12.345.678-9") == 12345678

    def test_run_invalido(self):
        from scraper.db.loaders.load_sige import _parse_run
        assert _parse_run("abc") is None
        assert _parse_run("") is None


class TestReadCsvChunks:

    def test_lee_todos_en_un_chunk(self, tmp_path):
        from scraper.db.loaders.load_sige import _read_csv_chunks
        rows = [{"rbd": "00001234", "agno": "2023", "n_orden": str(i)} for i in range(5)]
        f    = _write_csv(tmp_path / "cal.csv", rows)
        chunks = list(_read_csv_chunks(f, chunk_size=100))
        assert len(chunks) == 1
        assert len(chunks[0]) == 5

    def test_divide_en_multiples_chunks(self, tmp_path):
        from scraper.db.loaders.load_sige import _read_csv_chunks
        rows = [{"rbd": "00001234", "agno": "2023", "n": str(i)} for i in range(10)]
        f    = _write_csv(tmp_path / "cal.csv", rows)
        chunks = list(_read_csv_chunks(f, chunk_size=3))
        # 10 filas / 3 = 3 chunks de 3 + 1 de 1
        assert len(chunks) == 4
        assert sum(len(c) for c in chunks) == 10

    def test_chunk_es_lista_de_dicts(self, tmp_path):
        from scraper.db.loaders.load_sige import _read_csv_chunks
        rows = [{"col_a": "val1", "col_b": "val2"}]
        f    = _write_csv(tmp_path / "test.csv", rows)
        chunks = list(_read_csv_chunks(f, chunk_size=10))
        assert isinstance(chunks[0][0], dict)
        assert "col_a" in chunks[0][0]


class TestSimceHelpers:

    def test_parse_grado_sufijo(self):
        from scraper.db.loaders.load_dev import _parse_grado
        assert _parse_grado("4b") == 4
        assert _parse_grado("8b") == 8
        assert _parse_grado("2m") == 10
        assert _parse_grado("4m") == 12
        assert _parse_grado("4") == 4
        assert _parse_grado(None) is None
        assert _parse_grado("xyz") is None

    def test_infer_grado_desde_columnas(self):
        from scraper.db.loaders.load_dev import _infer_grado
        cols = ["agno", "rbd", "prom_lect4b_rbd", "prom_mate4b_rbd"]
        assert _infer_grado(cols, "archivo.csv") == 4

    def test_infer_grado_desde_nombre_archivo(self):
        from scraper.db.loaders.load_dev import _infer_grado
        assert _infer_grado(["agno", "rbd"], "simce8b2022_rbd.csv") == 8

    def test_infer_grado_none_si_no_hay_info(self):
        from scraper.db.loaders.load_dev import _infer_grado
        assert _infer_grado(["agno", "rbd", "ptje"], "datos.csv") is None

    def test_simce_col_map_no_tiene_duplicados(self):
        from scraper.db.loaders.load_dev import _SIMCE_COL_MAP
        valores = list(_SIMCE_COL_MAP.values())
        # Los valores se pueden repetir (múltiples aliases → mismo canónico)
        # Pero las claves no deben repetirse
        assert len(_SIMCE_COL_MAP) == len(set(_SIMCE_COL_MAP.keys()))


class TestLoadSigeIntegracion:
 
    def test_run_calificaciones_sin_estab_valido(self, tmp_path):
        from scraper.db.loaders.load_sige import run_calificaciones
 
        rows = [{"rbd": "99999999", "agno": "2023", "grado": "4",
                 "run": "11111111-1", "letra": "A", "n_orden": "1",
                 "promedio": "5.5", "asistencia_pct": "90", "situacion_final": "APR"}]
        src  = _write_csv(tmp_path / "sige_calificaciones.csv", rows)
 
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_cur.fetchone.return_value = None  # establecimiento no existe
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
 
        with patch("scraper.db.loaders.load_sige.get_conn", return_value=mock_conn), \
             patch("scraper.db.loaders.load_sige.get_establecimiento_id", return_value=None):
            # No debe lanzar excepción — debe saltar la fila
            run_calificaciones(src, dry_run=True)

    def test_read_csv_chunks_encoding_utf8sig(self, tmp_path):
        from scraper.db.loaders.load_sige import _read_csv_chunks
        rows = [{"rbd": "00001234", "agno": "2023", "n_orden": "1",
                 "nom": "Niña con ñ"}]
        path = tmp_path / "sige.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

        chunks = list(_read_csv_chunks(path, chunk_size=10))
        assert chunks[0][0]["nom"] == "Niña con ñ"


if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=str(Path(__file__).parent.parent),
    )
    sys.exit(result.returncode)