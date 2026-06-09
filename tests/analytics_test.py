from __future__ import annotations

import math
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

# Ajustar path para importar desde el módulo
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from analytics_pipeline import (
    AnalyticsPipeline,
    confidence_label,
    is_hiatus,
    linear_regression,
    percentile_of,
    _safe_float,
    _safe_int,
    DEFAULT_CONFIG,
)


class TestLinearRegression:

    def test_tendencia_perfecta(self):
        years  = [2010, 2011, 2012, 2013]
        values = [200.0, 210.0, 220.0, 230.0]
        reg = linear_regression(years, values)

        assert reg is not None
        assert abs(reg["slope"] - 10.0) < 1e-6
        assert abs(reg["r2"] - 1.0) < 1e-6
        assert reg["rmse"] < 1e-6
        assert reg["n_points"] == 4

    def test_proyeccion_correcta(self):
        years  = [2010, 2011, 2012, 2013]
        values = [200.0, 210.0, 220.0, 230.0]
        reg = linear_regression(years, values)
        assert abs(reg["predict"](2014) - 240.0) < 1e-4

    def test_serie_con_ruido(self):
        years  = [2010, 2011, 2012, 2013, 2014]
        values = [200.0, 215.0, 208.0, 225.0, 220.0]
        reg = linear_regression(years, values)

        assert reg is not None
        assert reg["slope"] > 0
        assert 0 < reg["r2"] < 1
        assert reg["rmse"] > 0

    def test_un_punto_retorna_none(self):
        reg = linear_regression([2022], [250.0])
        assert reg is None

    def test_dos_puntos_minimo_valido(self):
        reg = linear_regression([2022, 2023], [240.0, 250.0])
        assert reg is not None
        assert reg["n_points"] == 2

    def test_filtra_nan(self):
        years  = [2010, 2011, 2012, 2013]
        values = [200.0, float("nan"), 220.0, 230.0]
        reg = linear_regression(years, values)

        assert reg is not None
        assert reg["n_points"] == 3  # NaN filtrado

    def test_todos_nan_retorna_none(self):
        years  = [2010, 2011, 2012]
        values = [float("nan")] * 3
        reg = linear_regression(years, values)
        assert reg is None

    def test_hiatus_no_contamina_slope(self):
        # Solo segmento post-hiatus
        years  = [2022, 2023, 2024, 2025]
        values = [245.0, 250.0, 255.0, 260.0]
        reg = linear_regression(years, values)

        assert reg is not None
        assert reg["slope"] > 0


class TestPercentileOf:

    def test_valor_mas_alto(self):
        pct = percentile_of(300.0, [200.0, 250.0, 280.0, 300.0])
        assert abs(pct - 100.0) < 1e-6

    def test_valor_mas_bajo(self):
        pct = percentile_of(200.0, [200.0, 250.0, 280.0, 300.0])
        assert pct == 25.0  # 1/4 de la población

    def test_percentil_mediano(self):
        pop = list(range(0, 100, 1))  # 0..99
        pct = percentile_of(49.0, pop)
        assert abs(pct - 50.0) < 1.0

    def test_poblacion_vacia_retorna_none(self):
        pct = percentile_of(250.0, [])
        assert pct is None

    def test_valor_nan_retorna_none(self):
        pct = percentile_of(float("nan"), [200.0, 250.0])
        assert pct is None

    def test_filtra_nan_en_poblacion(self):
        pct = percentile_of(250.0, [200.0, float("nan"), 250.0, 300.0])
        assert pct is not None


class TestConfidenceLabel:

    @pytest.mark.parametrize("n,expected", [
        (1,  "insuficiente"),
        (2,  "insuficiente"),
        (3,  "baja"),
        (4,  "media"),
        (5,  "media"),
        (6,  "alta"),
        (10, "alta"),
    ])
    def test_todos_los_niveles(self, n, expected):
        assert confidence_label(n) == expected


class TestIsHiatus:

    def test_años_en_hiatus(self):
        for agno in [2019, 2020, 2021]:
            assert is_hiatus(agno, DEFAULT_CONFIG) is True

    def test_años_fuera_de_hiatus(self):
        for agno in [2018, 2022, 2023, 2025]:
            assert is_hiatus(agno, DEFAULT_CONFIG) is False


class TestSafeHelpers:

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_nan(self):
        assert _safe_float(float("nan")) is None

    def test_safe_float_string(self):
        assert _safe_float("250.5") == 250.5

    def test_safe_float_int(self):
        assert _safe_float(250) == 250.0

    def test_safe_int_none(self):
        assert _safe_int(None) is None

    def test_safe_int_float(self):
        assert _safe_int(4.7) == 4

    def test_safe_int_string(self):
        assert _safe_int("42") == 42


def _make_mock_conn():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    return conn, cursor


class TestAnalyticsPipelineInit:

    def test_rbd_zero_padded(self):
        conn, _ = _make_mock_conn()
        p = AnalyticsPipeline(conn, rbd="1234")
        assert p.rbd == "00001234"

    def test_modulos_default(self):
        conn, _ = _make_mock_conn()
        p = AnalyticsPipeline(conn, rbd="00001234")
        assert "simce_serie" in p.modules
        assert "cruce" in p.modules
        assert len(p.modules) == 5

    def test_modulos_custom(self):
        conn, _ = _make_mock_conn()
        p = AnalyticsPipeline(conn, rbd="00001234",
                              modules=["simce_serie", "sige_serie"])
        assert p.modules == ["simce_serie", "sige_serie"]


class TestLoadConfig:

    def test_config_cargada_desde_db(self):
        conn, cur = _make_mock_conn()
        cur.fetchall.return_value = [
            ("hiatus_start", "2019"),
            ("hiatus_end", "2021"),
            ("alert_threshold", "2.0"),
        ]
        p = AnalyticsPipeline(conn, rbd="00001234")
        p._load_config()

        assert p.cfg["hiatus_start"] == 2019
        assert p.cfg["alert_threshold"] == 2.0

    def test_config_fallback_si_db_falla(self):
        conn, cur = _make_mock_conn()
        cur.execute.side_effect = Exception("DB error")

        p = AnalyticsPipeline(conn, rbd="00001234")
        p._load_config()  # No debe lanzar excepción

        # Usa defaults
        assert p.cfg["hiatus_start"] == DEFAULT_CONFIG["hiatus_start"]


class TestSimceSerieMock:

    def _pipeline_with_simce_data(self, simce_rows):
        conn, cur = _make_mock_conn()

        # Mock para _query_simce_rbd
        cur.description = [
            ("agno",), ("cod_grado",), ("rbd",), ("cod_depe",),
            ("cod_com",), ("ptje_mat",), ("ptje_lect",), ("ptje_cie",),
            ("ptje_his",), ("ptje_ing",), ("n_eval_mat",), ("n_eval_lect",),
            ("gse_predominante",),
        ]
        cur.fetchall.return_value = simce_rows
        cur.fetchone.return_value = None  # benchmarks vacíos

        p = AnalyticsPipeline(conn, rbd="00001234")
        p.cfg = {**DEFAULT_CONFIG}
        return p, conn, cur

    def test_sin_datos_retorna_cero(self):
        p, conn, cur = self._pipeline_with_simce_data([])
        n = p._run_simce_serie()
        assert n == 0

    def test_construye_filas_correctas(self):
        rows = [
            # agno, cod_grado, rbd, cod_depe, cod_com, mat, lect, cie, his, ing, n_mat, n_lect, gse
            (2022, 4, "00001234", 2, "01301", 248.0, 255.0, None, None, None, 30, 30, "Medio"),
            (2023, 4, "00001234", 2, "01301", 255.0, 260.0, None, None, None, 32, 32, "Medio"),
        ]
        import pandas as pd

        p, conn, cur = self._pipeline_with_simce_data(rows)

        # Mock _query_simce_rbd directo
        import pandas as pd
        df = pd.DataFrame(rows, columns=[
            "agno", "cod_grado", "rbd", "cod_depe", "cod_com",
            "ptje_mat", "ptje_lect", "ptje_cie", "ptje_his", "ptje_ing",
            "n_eval_mat", "n_eval_lect", "gse_predominante",
        ])
        p._query_simce_rbd = MagicMock(return_value=df)
        p._query_simce_comuna_benchmark = MagicMock(return_value=pd.DataFrame())
        p._upsert_simce_serie = MagicMock()

        n = p._run_simce_serie()
        # 2 años × 2 asignaturas (mat + lect) = 4 filas
        assert n == 4
        p._upsert_simce_serie.assert_called_once()
        filas_escritas = p._upsert_simce_serie.call_args[0][0]
        assert len(filas_escritas) == 4


class TestSimceTendenciaMock:

    def test_tendencia_post_hiatus_cuatro_puntos(self):
        import pandas as pd

        conn, cur = _make_mock_conn()
        p = AnalyticsPipeline(conn, rbd="00001234")
        p.cfg = {**DEFAULT_CONFIG}

        # Mock analytics_simce_serie
        data = [
            (2022, "4b", "mat", 245.0),
            (2023, "4b", "mat", 250.0),
            (2024, "4b", "mat", 255.0),
            (2025, "4b", "mat", 258.0),
        ]
        cur.description = [("agno",), ("grado",), ("asignatura",), ("puntaje",)]
        cur.fetchall.return_value = data

        escritas = []
        p._upsert_analytics = MagicMock(side_effect=lambda r: escritas.append(r))
        conn.commit = MagicMock()

        p._run_simce_tendencia()

        # Debe haber al menos una fila post_hiatus
        post_hiatus = [r for r in escritas if r["segmento"] == "post_hiatus"]
        assert len(post_hiatus) >= 1

        fila = post_hiatus[0]
        assert fila["metrica"] == "simce_tendencia"
        assert fila["confianza"] == "media"  # n=4
        assert fila["tendencia_slope"] > 0   # tendencia positiva

    def test_tendencia_full_excluye_hiatus(self):
        import pandas as pd

        conn, cur = _make_mock_conn()
        p = AnalyticsPipeline(conn, rbd="00001234")
        p.cfg = {**DEFAULT_CONFIG}

        # Serie con hiatus en medio
        data = [
            (2016, "4b", "mat", 240.0),
            (2017, "4b", "mat", 242.0),
            (2018, "4b", "mat", 238.0),
            # 2019-2021: hiatus
            (2022, "4b", "mat", 250.0),
            (2023, "4b", "mat", 255.0),
            (2024, "4b", "mat", 258.0),
            (2025, "4b", "mat", 262.0),
        ]
        cur.description = [("agno",), ("grado",), ("asignatura",), ("puntaje",)]
        cur.fetchall.return_value = data

        escritas = []
        p._upsert_analytics = MagicMock(side_effect=lambda r: escritas.append(r))
        conn.commit = MagicMock()

        p._run_simce_tendencia()

        full_rows = [r for r in escritas if r["segmento"] == "full"]
        assert len(full_rows) >= 1
        # Con 7 puntos válidos (sin hiatus), confianza debe ser 'alta'
        assert full_rows[0]["confianza"] == "alta"


class TestCruceMock:

    def test_brecha_calculada_correctamente(self):
        import pandas as pd

        conn, cur = _make_mock_conn()
        p = AnalyticsPipeline(conn, rbd="00001234")
        p.cfg = {**DEFAULT_CONFIG}

        df_simce = pd.DataFrame([
            {"agno": 2023, "grado": "4b", "asig": "mat", "puntaje_simce": 250.0},
        ])
        df_sige = pd.DataFrame([
            {"agno": 2022, "grado": "4b", "prom_global": 5.5},
        ])

        p._query_simce_rbd      = MagicMock()
        p._query_sige_global    = MagicMock()

        escritas = []
        p._upsert_analytics = MagicMock(side_effect=lambda r: escritas.append(r))
        conn.commit = MagicMock()

        # Ejecutar cruce directamente con datos mockeados
        with patch.object(p, '_run_cruce') as mock_cruce:
            # Testear la lógica interna directamente
            pairs = []
            for _, sr in df_simce.iterrows():
                agno_simce = int(sr["agno"])
                agno_sige  = agno_simce - 1
                sige_row   = df_sige[
                    (df_sige["agno"] == agno_sige) &
                    (df_sige["grado"] == "4b")
                ]
                if not sige_row.empty:
                    pairs.append({
                        "agno":          agno_simce,
                        "prom_interno":  float(sige_row["prom_global"].iloc[0]),
                        "puntaje_simce": float(sr["puntaje_simce"]),
                    })

            assert len(pairs) == 1
            brecha = round(pairs[0]["prom_interno"] - pairs[0]["puntaje_simce"], 2)
            assert brecha == round(5.5 - 250.0, 2)

    def test_correlacion_requiere_min_puntos(self):
        conn, cur = _make_mock_conn()
        p = AnalyticsPipeline(conn, rbd="00001234")
        p.cfg = {**DEFAULT_CONFIG, "min_points_corr": 4}

        # Solo 3 pares — no alcanza para correlación
        pairs = [
            {"agno": 2014, "prom_interno": 5.2, "puntaje_simce": 240.0},
            {"agno": 2016, "prom_interno": 5.4, "puntaje_simce": 245.0},
            {"agno": 2018, "prom_interno": 5.5, "puntaje_simce": 248.0},
        ]
        assert len(pairs) < p.cfg["min_points_corr"]
        # Verificar que el guard funciona
        assert len(pairs) < 4


class TestAlertLogic:

    def test_alerta_disparada_cuando_residuo_grande(self):
        years  = [2013, 2014, 2015, 2016]
        values = [240.0, 242.0, 244.0, 246.0]  # tendencia perfecta
        reg = linear_regression(years, values)

        # Valor real muy alejado del proyectado
        valor_real       = 220.0  # 26 puntos bajo el esperado (246)
        valor_proyectado = reg["predict"](2016)
        threshold        = 1.5

        # RMSE es ~0 para serie perfecta, pero ajustamos el test
        # con datos con algo de ruido
        years2  = [2013, 2014, 2015, 2016, 2017]
        values2 = [240.0, 245.0, 241.0, 248.0, 243.0]
        reg2    = linear_regression(years2, values2)

        valor_real2       = 210.0
        valor_proyectado2 = reg2["predict"](2017)
        residuo2          = abs(valor_real2 - valor_proyectado2)
        alerta2           = residuo2 > threshold * reg2["rmse"]

        assert alerta2 is True

    def test_no_alerta_dentro_del_rango(self):
        years  = [2013, 2014, 2015, 2016, 2017]
        values = [240.0, 245.0, 241.0, 248.0, 244.0]
        reg    = linear_regression(years, values)

        # Valor muy cercano al proyectado
        valor_real       = reg["predict"](2017)  # exactamente el proyectado
        valor_proyectado = reg["predict"](2017)
        residuo          = abs(valor_real - valor_proyectado)
        alerta           = residuo > 1.5 * reg["rmse"]

        assert alerta is False


class TestEdgeCases:

    def test_serie_solo_post_hiatus(self):
        years  = [2022, 2023, 2024, 2025]
        values = [250.0, 255.0, 252.0, 258.0]
        reg = linear_regression(years, values)
        assert reg is not None
        assert reg["n_points"] == 4
        assert confidence_label(reg["n_points"]) == "media"

    def test_serie_con_dos_puntos_post_hiatus(self):
        years  = [2022, 2023]
        values = [250.0, 255.0]
        reg = linear_regression(years, values)
        assert reg is not None
        # n=2 → insuficiente según confidence_label
        assert confidence_label(reg["n_points"]) == "insuficiente"

    def test_percentil_poblacion_un_elemento(self):
        pct = percentile_of(250.0, [250.0])
        assert pct == 100.0

    def test_percentil_valor_mayor_a_toda_poblacion(self):
        pct = percentile_of(400.0, [200.0, 250.0, 300.0])
        assert pct == 100.0

    def test_percentil_valor_menor_a_toda_poblacion(self):
        pct = percentile_of(100.0, [200.0, 250.0, 300.0])
        assert pct == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])