import sys
import csv
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from scraper.normalizers.normalizer_base import (
    MANIFEST_FILE,
    NormalizerManifest,
    _infer_year,
    _normalize_col,
    _sha256,
    _sniff,
    prescan_columns,
    stream_source_to_writer,
    write_chunk,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


def _write_csv(path: Path, rows: list[dict], sep: str = ",", encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=sep)
        w.writeheader()
        w.writerows(rows)
    return path


_ALUMNOS_ROWS = [
    {"agno": "2023", "mrun": "11111111", "gen_alu": "1", "fec_nac_alu": "20050312",
     "rbd": "00001234", "cod_grado": "4", "let_cur": "A", "criterio_sep": "1"},
    {"agno": "2023", "mrun": "22222222", "gen_alu": "2", "fec_nac_alu": "20060815",
     "rbd": "00001234", "cod_grado": "4", "let_cur": "A", "criterio_sep": "0"},
]

_CARGOS_ROWS = [
    {"agno": "2023", "rbd": "00001234", "mrun": "99999999",
     "doc_fec_nac": "19800101", "cod_ense": "11", "n_horas": "44"},
]

_ESTAB_ROWS = [
    {"agno": "2023", "rbd": "00001234", "nom_rbd": "Escuela Test",
     "latitud": "-33,4489", "longitud": "-70,6693", "cod_depe": "1"},
]


class TestNormalizerManifest:

    def test_load_nuevo_sin_archivo(self, tmp_path):
        m = NormalizerManifest.load(tmp_path)
        assert m.entries == {}
        assert m.manifest_path == tmp_path / MANIFEST_FILE

    def test_is_processed_falso_para_nuevo(self, tmp_path, sample_csv):
        m = NormalizerManifest.load(tmp_path)
        assert not m.is_processed(sample_csv)

    def test_mark_normalized_y_is_processed(self, tmp_path, sample_csv):
        m = NormalizerManifest.load(tmp_path)
        m.mark_normalized(sample_csv, rows=10, output_file=tmp_path / "out.csv")
        assert m.is_processed(sample_csv)

    def test_pending_for_db_solo_normalized(self, tmp_path, sample_csv):
        m = NormalizerManifest.load(tmp_path)
        m.mark_normalized(sample_csv, rows=5, output_file=tmp_path / "out.csv")
        pending = m.pending_for_db()
        assert len(pending) == 1
        assert pending[0]["status"] == "normalized"

    def test_mark_loaded_cambia_status(self, tmp_path, sample_csv):
        m = NormalizerManifest.load(tmp_path)
        m.mark_normalized(sample_csv, rows=5, output_file=tmp_path / "out.csv")
        h = _sha256(sample_csv)
        m.mark_loaded(h)
        assert m.pending_for_db() == []
        assert m.entries[h]["status"] == "loaded"

    def test_persist_en_disco(self, tmp_path, sample_csv):
        m = NormalizerManifest.load(tmp_path)
        m.mark_normalized(sample_csv, rows=3, output_file=tmp_path / "out.csv")

        m2 = NormalizerManifest.load(tmp_path)
        assert m2.is_processed(sample_csv)

    def test_is_processed_falso_si_contenido_cambia(self, tmp_path, sample_csv):
        m = NormalizerManifest.load(tmp_path)
        m.mark_normalized(sample_csv, rows=2, output_file=tmp_path / "out.csv")
        # Modificar el archivo — el hash cambia
        sample_csv.write_text("agno,mrun\n2024,33333333\n")
        assert not m.is_processed(sample_csv)

    def test_loaded_at_se_registra(self, tmp_path, sample_csv):
        m = NormalizerManifest.load(tmp_path)
        m.mark_normalized(sample_csv, rows=1, output_file=tmp_path / "out.csv")
        h = _sha256(sample_csv)
        m.mark_loaded(h)
        assert "loaded_at" in m.entries[h]


class TestHelpers:

    def test_normalize_col_lowercase(self):
        assert _normalize_col("AGNO") == "agno"

    def test_normalize_col_espacios(self):
        assert _normalize_col("Nom RBD") == "nom_rbd"

    def test_normalize_col_bom(self):
        assert _normalize_col("\ufeffagno") == "agno"

    def test_normalize_col_guiones(self):
        assert _normalize_col("cod-depe") == "cod_depe"

    def test_normalize_col_doble_underscore(self):
        assert _normalize_col("cod__depe") == "cod_depe"

    def test_infer_year_desde_stem(self, tmp_path):
        f = tmp_path / "alumnos_2023_v2.csv"
        f.touch()
        assert _infer_year(f) == "2023"

    def test_infer_year_desde_directorio(self, tmp_path):
        d = tmp_path / "2022"
        d.mkdir()
        f = d / "datos.csv"
        f.touch()
        assert _infer_year(f) == "2022"

    def test_infer_year_none_si_no_hay(self, tmp_path):
        f = tmp_path / "datos_sin_año.csv"
        f.touch()
        assert _infer_year(f) is None

    def test_sniff_coma(self, tmp_path):
        f = _write_csv(tmp_path / "a.csv", _ALUMNOS_ROWS, sep=",")
        enc, sep = _sniff(f)
        assert enc is not None
        assert sep == ","

    def test_sniff_punto_coma(self, tmp_path):
        f = _write_csv(tmp_path / "a.csv", _ALUMNOS_ROWS, sep=";")
        enc, sep = _sniff(f)
        assert sep == ";"

    def test_sniff_latin1(self, tmp_path):
        rows = [{"agno": "2023", "nom_rbd": "Escuela Ñoño"}]
        f = _write_csv(tmp_path / "a.csv", rows, encoding="latin-1")
        enc, sep = _sniff(f)
        assert enc is not None

    def test_sniff_archivo_invalido(self, tmp_path):
        f = tmp_path / "binario.csv"
        f.write_bytes(b"\x00\x01\x02\x03")
        enc, sep = _sniff(f)
        assert enc is None


class TestWriteChunk:

    def test_escribe_filas(self, tmp_path):
        out = tmp_path / "out.csv"
        fieldnames = ["agno", "mrun", "gen_alu", "_source_file"]
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            chunk = pd.DataFrame(_ALUMNOS_ROWS)
            n = write_chunk(chunk, writer, "2023", "test.csv", {})
        assert n == 2
        df = pd.read_csv(out)
        assert len(df) == 2
        assert "_source_file" in df.columns

    def test_inyecta_agno_si_falta(self, tmp_path):
        out = tmp_path / "out.csv"
        rows = [{"mrun": "11111111", "rbd": "00001234"}]
        fieldnames = ["agno", "mrun", "rbd", "_source_file"]
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            chunk = pd.DataFrame(rows)
            write_chunk(chunk, writer, "2021", "datos_2021.csv", {})
        df = pd.read_csv(out, dtype=str)
        assert df.iloc[0]["agno"] == "2021"

    def test_aplica_col_transform(self, tmp_path):
        out = tmp_path / "out.csv"
        rows = [{"agno": "2023", "fec_nac_alu": "20050312"}]
        fieldnames = ["agno", "fec_nac_alu", "_source_file"]
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            chunk = pd.DataFrame(rows)
            write_chunk(chunk, writer, None, "x.csv",
                        {"fec_nac_alu": lambda v: f"{v[:4]}-{v[4:6]}-{v[6:]}" if len(str(v)) == 8 else v})
        df = pd.read_csv(out, dtype=str)
        assert df.iloc[0]["fec_nac_alu"] == "2005-03-12"

    def test_normaliza_nombres_de_columnas(self, tmp_path):
        out = tmp_path / "out.csv"
        fieldnames = ["agno", "nom_rbd", "_source_file"]
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            # Columnas con formato raro
            chunk = pd.DataFrame([{"AGNO": "2023", "NOM RBD": "Escuela"}])
            write_chunk(chunk, writer, None, "x.csv", {})
        df = pd.read_csv(out, dtype=str)
        assert "nom_rbd" in df.columns


class TestStreamSource:

    def test_lee_csv_completo(self, tmp_path):
        src = _write_csv(tmp_path / "alumnos_2023.csv", _ALUMNOS_ROWS)
        out = tmp_path / "out.csv"
        fieldnames = ["agno", "mrun", "gen_alu", "_source_file"]
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            n = stream_source_to_writer(src, writer, chunk_size=10, col_transforms={})
        assert n == 2

    def test_retorna_none_si_encoding_invalido(self, tmp_path):
        f = tmp_path / "datos.csv"
        f.write_bytes(b"\x00\x01\x02\x03\x04\x05")
        out = tmp_path / "out.csv"
        fieldnames = ["col", "_source_file"]
        with open(out, "w", newline="", encoding="utf-8") as f2:
            writer = csv.DictWriter(f2, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            n = stream_source_to_writer(f, writer, chunk_size=10, col_transforms={})
        assert n is None

    def test_infiere_agno_desde_nombre(self, tmp_path):
        rows = [{"mrun": "11111111", "rbd": "00001234"}]
        src = _write_csv(tmp_path / "datos_2019.csv", rows)
        out = tmp_path / "out.csv"
        fieldnames = ["agno", "mrun", "rbd", "_source_file"]
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            stream_source_to_writer(src, writer, chunk_size=10, col_transforms={})
        df = pd.read_csv(out, dtype=str)
        assert df.iloc[0]["agno"] == "2019"


class TestPrescanColumns:

    def test_detecta_columnas_de_csv(self, tmp_path):
        _write_csv(tmp_path / "alumnos_2023.csv", _ALUMNOS_ROWS)
        priority = ["agno", "mrun", "rbd"]
        cols = prescan_columns(tmp_path, priority, ["_source_file"], no_rar=True)
        assert "agno" in cols
        assert "mrun" in cols
        assert "_source_file" in cols

    def test_prioridad_respetada(self, tmp_path):
        _write_csv(tmp_path / "datos.csv", _ALUMNOS_ROWS)
        priority = ["agno", "mrun", "gen_alu"]
        cols = prescan_columns(tmp_path, priority, ["_source_file"], no_rar=True)
        # Las columnas priority deben aparecer antes que las extra
        idx_agno = cols.index("agno")
        idx_mrun = cols.index("mrun")
        assert idx_agno < idx_mrun

    def test_columnas_de_multiples_csvs(self, tmp_path):
        rows_a = [{"agno": "2022", "mrun": "111", "campo_a": "x"}]
        rows_b = [{"agno": "2023", "mrun": "222", "campo_b": "y"}]
        _write_csv(tmp_path / "a.csv", rows_a)
        _write_csv(tmp_path / "b.csv", rows_b)
        cols = prescan_columns(tmp_path, ["agno", "mrun"], ["_source_file"], no_rar=True)
        assert "campo_a" in cols
        assert "campo_b" in cols

    def test_directorio_vacio_retorna_vacio(self, tmp_path):
        cols = prescan_columns(tmp_path, ["agno"], ["_source_file"], no_rar=True)
        assert cols == ["agno", "_source_file"]


class TestMineducNormalizers:

    def test_alumnos_normalize_full(self, tmp_path):
        from scraper.normalizers.mineduc_alumnos_normalizer import normalize
        src = tmp_path / "raw"
        _write_csv(src / "alumnos_2023.csv", _ALUMNOS_ROWS)
        out = tmp_path / "processed" / "mineduc_alumnos.csv"

        stats = normalize(src, out, mode="full", no_rar=True)

        assert out.exists()
        assert stats["total_rows"] == 2
        df = pd.read_csv(out, dtype=str)
        assert "mrun" in df.columns
        assert "_source_file" in df.columns
        assert df.iloc[0]["fec_nac_alu"] == "2005-03-12"

    def test_alumnos_normalize_delta_skip(self, tmp_path):
        from scraper.normalizers.mineduc_alumnos_normalizer import normalize
        src = tmp_path / "raw"
        csv_file = _write_csv(src / "alumnos_2023.csv", _ALUMNOS_ROWS)
        out = tmp_path / "processed" / "mineduc_alumnos.csv"

        # Primera corrida — procesa
        normalize(src, out, mode="full", no_rar=True)
        # Segunda corrida en delta — debe saltarlo
        stats = normalize(src, out, mode="delta", no_rar=True)

        assert "alumnos_2023.csv" in stats["skipped"]

    def test_cargos_normalize_full(self, tmp_path):
        from scraper.normalizers.mineduc_cargos_normalizer import normalize
        src = tmp_path / "raw"
        _write_csv(src / "cargos_2023.csv", _CARGOS_ROWS)
        out = tmp_path / "processed" / "mineduc_cargos.csv"

        stats = normalize(src, out, mode="full", no_rar=True)

        assert out.exists()
        assert stats["total_rows"] == 1
        df = pd.read_csv(out, dtype=str)
        assert df.iloc[0]["doc_fec_nac"] == "1980-01-01"

    def test_establecimientos_normalize_full(self, tmp_path):
        from scraper.normalizers.mineduc_establecimientos_normalizer import normalize
        src = tmp_path / "raw"
        _write_csv(src / "establecimientos_2023.csv", _ESTAB_ROWS)
        out = tmp_path / "processed" / "mineduc_establecimientos.csv"

        stats = normalize(src, out, mode="full", no_rar=True)

        assert out.exists()
        df = pd.read_csv(out, dtype=str)
        assert df.iloc[0]["latitud"] == "-33.4489"
        assert df.iloc[0]["longitud"] == "-70.6693"

    def test_manifest_pending_for_db_tras_normalize(self, tmp_path):
        from scraper.normalizers.mineduc_alumnos_normalizer import normalize
        src = tmp_path / "raw"
        _write_csv(src / "alumnos_2023.csv", _ALUMNOS_ROWS)
        out = tmp_path / "processed" / "mineduc_alumnos.csv"

        stats = normalize(src, out, mode="full", no_rar=True)
        assert len(stats["pending_for_db"]) >= 1
        assert stats["pending_for_db"][0]["status"] == "normalized"

    def test_manifest_mark_loaded_limpia_pending(self, tmp_path):
        from scraper.normalizers.mineduc_alumnos_normalizer import normalize
        src = tmp_path / "raw"
        _write_csv(src / "alumnos_2023.csv", _ALUMNOS_ROWS)
        out = tmp_path / "processed" / "mineduc_alumnos.csv"

        stats  = normalize(src, out, mode="full", no_rar=True)
        entry  = stats["pending_for_db"][0]
        proc   = out.parent

        manifest = NormalizerManifest.load(proc)
        manifest.mark_loaded(entry["source_hash"])

        assert manifest.pending_for_db() == []


class TestSimceNormalizer:

    def test_read_csv_normaliza_rbd(self, tmp_path):
        from scraper.normalizers.simce_normalizer import _read_csv
        rows = [{"agno": "2022", "grado": "4b", "rbd": "1234",
                 "prom_lect4b_rbd": "255", "prom_mate4b_rbd": "260"}]
        f = _write_csv(tmp_path / "simce4b2022_rbd_publica_final.csv", rows, sep="|")
        # Reescribir con encoding latin-1 como SIMCE real
        pd.DataFrame(rows).to_csv(f, sep="|", index=False, encoding="latin-1")
        df = _read_csv(f)
        assert df is not None
        assert df.iloc[0]["rbd"] == "00001234"

    def test_export_granularidad_upsert(self, tmp_path):
        from scraper.normalizers.simce_normalizer import _export_granularidad
        df1 = pd.DataFrame([{"agno": "2022", "grado": "4b", "rbd": "00001234", "ptje": "255"}])
        df2 = pd.DataFrame([{"agno": "2022", "grado": "4b", "rbd": "00001234", "ptje": "260"}])

        out = _export_granularidad("rbd", [df1], tmp_path)
        assert out.exists()

        # Segunda exportación con dato actualizado — debe sobrescribir
        out2 = _export_granularidad("rbd", [df2], tmp_path)
        df_final = pd.read_csv(out2, dtype=str)
        assert len(df_final) == 1
        assert df_final.iloc[0]["ptje"] == "260"

    def test_export_granularidad_agrega_nuevos(self, tmp_path):
        from scraper.normalizers.simce_normalizer import _export_granularidad
        df1 = pd.DataFrame([{"agno": "2021", "grado": "4b", "rbd": "00001234", "ptje": "250"}])
        df2 = pd.DataFrame([{"agno": "2022", "grado": "4b", "rbd": "00001234", "ptje": "255"}])

        _export_granularidad("rbd", [df1], tmp_path)
        _export_granularidad("rbd", [df2], tmp_path)

        df_final = pd.read_csv(tmp_path / "simce__rbd.csv", dtype=str)
        assert len(df_final) == 2


class TestSigeNormalizer:

    def test_parse_filename_valido(self):
        from scraper.normalizers.sige_normalizer import _parse_filename
        meta = _parse_filename("00001234_23_4_A_15032023_120000.pdf")
        assert meta is not None
        assert meta["rbd"] == "00001234"
        assert meta["grado"] == 4
        assert meta["letra"] == "A"
        assert meta["agno_file"] == 2023
        assert meta["fecha_acta"] == "15/03/2023"

    def test_parse_filename_invalido(self):
        from scraper.normalizers.sige_normalizer import _parse_filename
        assert _parse_filename("archivo_sin_formato.pdf") is None
        assert _parse_filename("") is None

    def test_nota_float(self):
        from scraper.normalizers.sige_normalizer import _nota
        assert _nota("6.5") == 6.5
        assert _nota("6,5") == 6.5
        assert _nota("") is None
        assert _nota("-") is None
        assert _nota(None) is None

    def test_safe_int(self):
        from scraper.normalizers.sige_normalizer import _safe_int
        assert _safe_int("3") == 3
        assert _safe_int("3.0") == 3
        assert _safe_int("abc") is None
        assert _safe_int(None) is None

    def test_s_limpia(self):
        from scraper.normalizers.sige_normalizer import _s
        assert _s("  texto  ") == "texto"
        assert _s("None") is None
        assert _s("") is None
        assert _s(None) is None

    def test_rbd_en_meta(self):
        from scraper.normalizers.sige_normalizer import _parse_filename
        meta = _parse_filename("123_23_8_B_01082022_083000.pdf")
        assert meta["rbd"] == "00000123"


@pytest.fixture
def sample_csv(tmp_path):
    return _write_csv(tmp_path / "sample.csv", _ALUMNOS_ROWS)


if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=str(Path(__file__).parent.parent),
    )
    sys.exit(result.returncode)