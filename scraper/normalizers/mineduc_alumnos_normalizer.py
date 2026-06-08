import argparse
import csv
import logging
import sys
from pathlib import Path

try:
    from .normalizer_base import (
        CHUNK_SIZE, NormalizerManifest,
        iter_sources, prescan_columns, stream_source_to_writer,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scraper.normalizers.normalizer_base import (
        CHUNK_SIZE, NormalizerManifest,
        iter_sources, prescan_columns, stream_source_to_writer,
    )

logger = logging.getLogger(__name__)

_INPUT_DEFAULT  = "data/mineduc/raw/alumnos"
_OUTPUT_DEFAULT = "data/mineduc/processed/mineduc_alumnos.csv"

# Orden de columnas en el output — las que no estén aquí van al final
_PRIORITY_COLS = [
    "agno", "mrun", "gen_alu", "fec_nac_alu", "fec_defun_alu",
    "criterio_sep", "prioritario_alu", "preferente_alu", "ben_sep",
    "rbd", "dgv_rbd", "nom_rbd",
    "cod_reg_rbd", "nom_reg_rbd_a", "cod_pro_rbd", "cod_com_rbd", "nom_com_rbd",
    "cod_deprov_rbd", "nom_deprov_rbd",
    "cod_depe", "cod_depe2", "rural_rbd", "estado_estab",
    "nombre_slep", "convenio_sep", "año_ingreso_sep", "clasificacion_sep", "ee_gratuito",
    "cod_ense", "cod_ense2", "cod_ense3",
    "cod_grado", "cod_grado2", "let_cur", "cod_jor", "grado_sep",
    "let_rbd", "num_rbd",
]
_META_COLS = ["_source_file"]

# Alias para columnas con nombres inconsistentes entre años
_COL_ALIASES = {
    "anio": "agno", "año": "agno", "year": "agno",
    "cod_mrun": "mrun",
    "cod_rbd": "rbd",
    "cod_gen_alu": "gen_alu", "sexo": "gen_alu",
    "cod_sep": "criterio_sep", "condicion_sep": "criterio_sep", "condicion": "criterio_sep",
    "cod_reg_rbd": "cod_reg_rbd", "cod_region": "cod_reg_rbd",
    "cod_com_rbd": "cod_com_rbd", "cod_comuna_rbd": "cod_com_rbd",
    "cod_depe": "cod_depe", "dependencia": "cod_depe",
    "cod_rural": "rural_rbd",
    "cod_ense": "cod_ense", "cod_grado": "cod_grado", "grado": "cod_grado",
}


def _normalize_fec_nac(val: str) -> str:
    v = str(val).strip()
    if len(v) == 8 and v.isdigit():
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    if len(v) == 6 and v.isdigit():
        return f"{v[:4]}-{v[4:]}"
    return v


def _apply_aliases(chunk):
    chunk.columns = [_COL_ALIASES.get(c, c) for c in chunk.columns]
    return chunk


def normalize(
    source_dir: Path,
    output_path: Path,
    mode: str = "full",
    no_rar: bool = False,
    chunk_size: int = CHUNK_SIZE,
    forced_enc: str | None = None,
) -> dict:
    source_dir  = Path(source_dir).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = NormalizerManifest.load(output_path.parent)

    logger.info("Fase 1: prescan de columnas")
    final_cols = prescan_columns(source_dir, _PRIORITY_COLS, _META_COLS, no_rar, forced_enc)
    if not final_cols:
        raise FileNotFoundError(f"Sin fuentes procesables en {source_dir}")
    logger.info("Esquema: %d columnas", len(final_cols))

    total_rows = 0
    skipped: list[str] = []
    processed_this_run: list[Path] = []

    logger.info("Fase 2: procesando fuentes")
    with open(output_path, "w", newline="", encoding="utf-8-sig") as out_fh:
        writer = csv.DictWriter(out_fh, fieldnames=final_cols, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()

        for csv_path, is_temp in iter_sources(source_dir, no_rar):
            try:
                if mode == "delta" and manifest.is_processed(csv_path):
                    logger.info("  skip (ya procesado): %s", csv_path.name)
                    skipped.append(csv_path.name)
                    continue

                rows = stream_source_to_writer(
                    csv_path, writer, chunk_size,
                    col_transforms={"fec_nac_alu": _normalize_fec_nac},
                    forced_enc=forced_enc,
                )
                if rows is None:
                    skipped.append(csv_path.name)
                else:
                    total_rows += rows
                    processed_this_run.append(csv_path)
            finally:
                if is_temp and csv_path.exists():
                    csv_path.unlink()

    # Registrar en manifest después de escribir exitosamente
    for src_path in processed_this_run:
        try:
            manifest.mark_normalized(src_path, total_rows, output_path)
        except Exception:
            pass  # Si el path temp ya no existe, ignorar

    stats = {
        "total_rows": total_rows,
        "skipped": skipped,
        "output_columns": final_cols,
        "pending_for_db": manifest.pending_for_db(),
    }
    logger.info("Completado: %d filas → %s", total_rows, output_path)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Normaliza CSVs de Alumnos SEP")
    parser.add_argument("--input",   default=_INPUT_DEFAULT,  metavar="DIR")
    parser.add_argument("--output",  default=_OUTPUT_DEFAULT, metavar="FILE")
    parser.add_argument("--mode",    default="full", choices=["full", "delta"],
                        help="full=reprocesa todo, delta=solo archivos nuevos")
    parser.add_argument("--no-rar",  action="store_true")
    parser.add_argument("--chunk",   default=CHUNK_SIZE, type=int, metavar="N")
    parser.add_argument("--encoding", default=None, metavar="ENC")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    stats = normalize(
        source_dir  = Path(args.input),
        output_path = Path(args.output),
        mode        = args.mode,
        no_rar      = args.no_rar,
        chunk_size  = args.chunk,
        forced_enc  = args.encoding,
    )

    print(f"\n{'═'*52}")
    print(f"  Output  : {args.output}")
    print(f"  Filas   : {stats['total_rows']:,}")
    print(f"  Columnas: {len(stats['output_columns'])}")
    print(f"  Omitidos: {len(stats['skipped'])}")
    print(f"  Pendiente DB: {len(stats['pending_for_db'])} archivo(s)")
    print(f"{'═'*52}")
    sys.exit(0)


if __name__ == "__main__":
    main()