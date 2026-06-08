from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class PipelineResult(BaseModel):
    job_id:        str
    status:        Literal["ok", "error", "partial"]
    source:        Literal["mineduc", "simce", "sige"]
    pipeline:      str                  # nombre del sub-pipeline, e.g. "load_alumnos"
    rows_read:     int = 0
    rows_inserted: int = 0
    rows_updated:  int = 0
    rows_skipped:  int = 0
    error:         str | None = None
    started_at:    datetime
    finished_at:   datetime


class JobStatus(BaseModel):
    job_id:       str
    status:       Literal["running", "ok", "error", "partial"]
    source:       str
    pipeline:     str
    progress_pct: int | None = None     # 0-100, None si no disponible
    started_at:   datetime
    finished_at:  datetime | None = None
    result:       PipelineResult | None = None


class FileEntry(BaseModel):
    path:         str
    sha256:       str
    size_bytes:   int
    processed:    bool
    processed_at: datetime | None = None
    job_id:       str | None = None


class RunRequest(BaseModel):
    step:        Literal["download", "normalize", "load", "all"] = "all"
    source_path: str | None = None      # ruta opcional; si omite usa el default del pipeline
    dry_run:     bool = False


class RunResponse(BaseModel):
    job_id: str
    status: Literal["running", "queued"] = "running"


class UploadResponse(BaseModel):
    job_id:         str
    files_received: int
    status:         Literal["running"] = "running"