import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

try:
    from .normalizer_base import NormalizerManifest, _sha256
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scraper.normalizers.normalizer_base import NormalizerManifest, _sha256

logger = logging.getLogger(__name__)

_INPUT_DEFAULT  = "data/simce/raw"
_OUTPUT_DEFAULT = "data/simce/processed"

_GRANULARIDADES = ("rbd", "comuna", "deprov", "region")

# Patrón del nombre de CSV dentro del RAR
import re
_CSV_RE = re.compile(
    r"simce(\d+[bm])(\d{4})_(rbd|comuna|deprov|region)_publica_final\.csv$",
    re.IGNORECASE,
)

# Claves de deduplicación por granularidad al hacer upsert
_UPSERT_KEYS = {
    "rbd":    ["agno", "grado", "rbd"],
    "comuna": ["agno", "grado", "cod_com"],
    "deprov": ["agno", "grado", "cod_deprov"],
    "region": ["agno", "grado", "cod_reg"],
}


def _extract_rar(rar_path: Path, tmpdir: str) -> list[Path]:
    result = subprocess.run(
        ["unrar", "x", "-y", str(rar_path), tmpdir],
        capture_output=True, text=True,
    )
    if result.returncode not in (0, 1):
        logger.error("unrar falló en %s: %s", rar_path.name, result.stderr[:500])
        return []
    return list(Path(tmpdir).rglob("*.csv"))


def _read_csv(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path, sep="|", encoding="latin-1", dtype=str)
    except Exception as exc:
        logger.error("Error leyendo %s: %s", path.name, exc)
        return None

    df.columns = [c.strip().lower() for c in df.columns]
    df = df.dropna(how="all").reset_index(drop=True)

    if "rbd" in df.columns:
        df["rbd"] = df["rbd"].astype(str).str.extract(r"(\d+)")[0].str.zfill(8)

    return df


def _export_granularidad(gran: str, dfs: list[pd.DataFrame], output_dir: Path) -> Path:
    output = output_dir / f"simce__{gran}.csv"
    df_new = pd.concat(dfs, ignore_index=True).astype(str)

    if output.exists():
        df_old = pd.read_csv(output, dtype=str)
        keys   = [k for k in _UPSERT_KEYS[gran] if k in df_new.columns and k in df_old.columns]
        df_new = (
            pd.concat([df_old, df_new], ignore_index=True)
            .drop_duplicates(subset=keys, keep="last")
            .reset_index(drop=True)
        )

    df_new.to_csv(output, index=False, encoding="utf-8-sig")
    logger.info("simce__%s.csv → %d filas", gran, len(df_new))
    return output


def normalize(
    source_dir: Path,
    output_dir: Path,
    mode: str = "full",
) -> dict:
    source_dir = Path(source_dir).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = NormalizerManifest.load(output_dir)
    frames: dict[str, list[pd.DataFrame]] = {g: [] for g in _GRANULARIDADES}
    stats = {"processed": 0, "skipped": 0, "outputs": {}}

    rars = sorted(source_dir.glob("*.rar")) + sorted(source_dir.glob("*.rar.rar"))
    logger.info("%d RAR(s) encontrados", len(rars))

    for rar_path in rars:
        if mode == "delta" and manifest.is_processed(rar_path):
            logger.info("skip: %s", rar_path.name)
            stats["skipped"] += 1
            continue

        with tempfile.TemporaryDirectory() as tmpdir:
            csvs = _extract_rar(rar_path, tmpdir)
            if not csvs:
                continue

            rows_this_rar = 0
            for csv_path in csvs:
                m = _CSV_RE.search(csv_path.name)
                if not m:
                    continue
                gran = m.group(3).lower()
                df   = _read_csv(csv_path)
                if df is not None:
                    frames[gran].append(df)
                    rows_this_rar += len(df)

            if rows_this_rar:
                stats["processed"] += 1
                manifest.mark_normalized(rar_path, rows_this_rar, output_dir)

    for gran, dfs in frames.items():
        if dfs:
            out = _export_granularidad(gran, dfs, output_dir)
            stats["outputs"][gran] = str(out)

    stats["pending_for_db"] = manifest.pending_for_db()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Normaliza RARs SIMCE")
    parser.add_argument("--input",   default=_INPUT_DEFAULT,  metavar="DIR")
    parser.add_argument("--output",  default=_OUTPUT_DEFAULT, metavar="DIR")
    parser.add_argument("--mode",    default="full", choices=["full", "delta"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    stats = normalize(
        source_dir = Path(args.input),
        output_dir = Path(args.output),
        mode       = args.mode,
    )

    print(f"\n{'═'*52}")
    for gran, path in stats["outputs"].items():
        print(f"  {gran:<10}: {path}")
    print(f"  Procesados : {stats['processed']}")
    print(f"  Omitidos   : {stats['skipped']}")
    print(f"  Pendiente DB: {len(stats['pending_for_db'])} archivo(s)")
    print(f"{'═'*52}")
    sys.exit(0)


if __name__ == "__main__":
    main()