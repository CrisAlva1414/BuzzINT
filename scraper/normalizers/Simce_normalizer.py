"""
python '/home/pc01/Proyectos/BuzzINT/scraper/normalizers/Simce_normalizer.py' '/home/pc01/Proyectos/BuzzINT/data/simce/raw' '/home/pc01/Proyectos/BuzzINT/data/simce/processed'
"""

from __future__ import annotations

import io
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import rarfile

logger = logging.getLogger(__name__)

# Patrón del nombre de archivo CSV dentro del RAR
_CSV_RE = re.compile(
    r"simce(\d+[bm])(\d{4})_(rbd|comuna|deprov|region)_publica_final\.csv$",
    re.IGNORECASE,
)

# Patrón del nombre de archivo RAR fuente para inferir nivel/año como fallback
_RAR_NAME_RE = re.compile(
    r"simce[_\s]+(\d{4})[_\s]+(\d+)[°º][_\s]*(b[aá]sico|medio)",
    re.IGNORECASE,
)

GRANULARIDADES = ("rbd", "comuna", "deprov", "region")


class SimceNormalizer:

    def __init__(self, raw_dir: Path, output_dir: Path) -> None:
        self.raw_dir    = Path(raw_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        rarfile.UNRAR_TOOL = "unrar"

    def run(self) -> dict[str, Path]:
        frames: dict[str, list[pd.DataFrame]] = {g: [] for g in GRANULARIDADES}

        rars = sorted(self.raw_dir.glob("*.rar")) + sorted(self.raw_dir.glob("*.rar.rar"))
        logger.info("Encontrados %d RARs en %s", len(rars), self.raw_dir)

        for rar_path in rars:
            self._process_rar(rar_path, frames)

        outputs: dict[str, Path] = {}
        for gran, dfs in frames.items():
            if not dfs:
                continue
            outputs[gran] = self._export(gran, dfs)

        return outputs


    def _process_rar(self, rar_path: Path, frames: dict[str, list]) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:

            result = subprocess.run(
                ["unrar", "x", "-y", str(rar_path), tmpdir],
                capture_output=True,
                text=True,
            )

            if result.returncode not in (0, 1):
                logger.error(
                    "unrar falló en %s\nSTDOUT:\n%s\nSTDERR:\n%s",
                    rar_path.name,
                    result.stdout[:1000],
                    result.stderr[:1000],
                )
                return

            csvs = list(Path(tmpdir).rglob("*.csv"))

            if not csvs:
                logger.warning(
                    "No se encontraron CSVs dentro de %s",
                    rar_path.name,
                )
                return

            logger.info(
                "%s → %d CSV(s) encontrados",
                rar_path.name,
                len(csvs),
            )

            for csv_path in csvs:

                logger.debug("CSV encontrado: %s", csv_path)

                m = _CSV_RE.search(csv_path.name)

                if not m:
                    logger.debug(
                        "CSV no coincide con patrón esperado: %s",
                        csv_path.name,
                    )
                    continue

                gran = m.group(3).lower()

                df = self._read_csv(csv_path, rar_path)

                if df is not None:
                    frames[gran].append(df)

    def _read_csv(self, path: Path, source_rar: Path) -> pd.DataFrame | None:
        try:
            df = pd.read_csv(path, sep="|", encoding="latin-1", dtype=str)
        except Exception as exc:
            logger.error("Error leyendo %s: %s", path.name, exc)
            return None

        df.columns = [c.strip().lower() for c in df.columns]
        df = df.dropna(how="all").reset_index(drop=True)

        # Normalizar rbd a 8 dígitos
        if "rbd" in df.columns:
            df["rbd"] = (
                df["rbd"].astype(str)
                .str.extract(r"(\d+)")[0]
                .str.zfill(8)
            )

        # Asegurar que agno y grado existen (ya vienen en el CSV)
        if "agno" not in df.columns:
            logger.warning("Sin columna 'agno' en %s — inferiendo del nombre", path.name)
            m = _CSV_RE.search(path.name)
            if m:
                df.insert(0, "agno", m.group(2))
                df.insert(1, "grado", m.group(1).lower())

        logger.debug("Leído: %s (%d filas × %d cols)", path.name, len(df), len(df.columns))
        return df


    def _export(self, gran: str, dfs: list[pd.DataFrame]) -> Path:
        output = self.output_dir / f"simce__{gran}.csv"

        df_new = pd.concat(dfs, ignore_index=True)

        # Upsert si ya existe
        if output.exists():
            df_old = pd.read_csv(output, dtype=str)
            keys   = _upsert_keys(gran)
            df_new = df_new.astype(str)
            df_old = df_old.astype(str)
            df_combined = (
                pd.concat([df_old, df_new], ignore_index=True)
                .drop_duplicates(subset=keys, keep="last")
                .reset_index(drop=True)
            )
        else:
            df_combined = df_new

        df_combined.to_csv(output, index=False, encoding="utf-8-sig")
        logger.info("simce__%s.csv → %d filas", gran, len(df_combined))
        return output


def _upsert_keys(gran: str) -> list[str]:
    base = ["agno", "grado"]
    geo  = {
        "rbd":    ["rbd"],
        "comuna": ["cod_com"],
        "deprov": ["cod_deprov"],
        "region": ["cod_reg"],
    }
    return base + geo.get(gran, [])


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) < 3:
        print("Uso: python simce__Normalizer.py <raw_dir> <output_dir>")
        sys.exit(1)

    norm    = SimceNormalizer(raw_dir=Path(sys.argv[1]), output_dir=Path(sys.argv[2]))
    outputs = norm.run()

    for gran, path in outputs.items():
        df = pd.read_csv(path, dtype=str)
        print(f"\nsimce__{gran}.csv — {len(df)} filas × {len(df.columns)} cols")
        print(f"  años: {sorted(df['agno'].unique())}")
        print(f"  grados: {sorted(df['grado'].unique())}")
        print(f"  cols: {list(df.columns[:8])} ...")