"""Database models using SQLAlchemy ORM."""
from sqlalchemy import Column, String, DateTime, Integer, Text, UUID, JSON
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()


class Establecimiento(Base):
    """Educational institution model."""
    
    __tablename__ = "establecimientos"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rbd = Column(String(10), unique=True, nullable=False, index=True)
    nombre = Column(String(255), nullable=False)
    comuna = Column(String(100))
    provincia = Column(String(100))
    region = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DocumentoRaw(Base):
    """Raw document storage (Bronze layer)."""
    
    __tablename__ = "documentos_raw"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fuente = Column(String(50), nullable=False, index=True)
    url = Column(Text, nullable=False)
    hash_contenido = Column(String(64), nullable=False, index=True)
    contenido = Column(Text, nullable=False)
    meta_info = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DatoNormalizado(Base):
    """Normalized data storage (Silver layer)."""
    
    __tablename__ = "datos_normalizados"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rbd = Column(String(10), nullable=False, index=True)
    fuente = Column(String(50), nullable=False)
    tipo_dato = Column(String(100), nullable=False)
    contenido_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JobEjecucion(Base):
    """Scheduler job execution log."""
    
    __tablename__ = "job_ejecuciones"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre_job = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)  # success, failed, running
    mensaje = Column(Text)
    registros_procesados = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True))
