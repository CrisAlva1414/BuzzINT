# README — Scrapers

> Referencia técnica para desarrollar y mantener los extractores del pipeline.
> Basado en los patrones establecidos por `simce_downloader.py` y `sige_downloader.py`.

---

## Índice

1. [Patrones compartidos](#1-patrones-compartidos)
2. [Funciones base reutilizables](#2-funciones-base-reutilizables)
3. [Tipos de scraper](#3-tipos-de-scraper)
4. [Scrapers existentes](#4-scrapers-existentes)
5. [Scrapers pendientes](#5-scrapers-pendientes)
6. [Checklist para un scraper nuevo](#6-checklist-para-un-scraper-nuevo)

---

## 1. Patrones compartidos

Ambos scrapers existentes convergen en el mismo flujo:

```
1. fetch_links / fetch_catalog   →  descubrir qué hay para descargar
2. dedup check (hash + manifest) →  saltear lo que no cambió
3. download                      →  obtener el contenido binario
4. persist + hash                →  guardar en disco, registrar en manifest
5. checkpoint                    →  save_manifest() por lote, no solo al final
6. stats                         →  ok / skip / fail / empty al terminar
```

Toda la trazabilidad vive en el **manifest** (`manifest.json`) que acompaña cada directorio de salida. Es la única fuente de verdad sobre qué está descargado, cuándo, y con qué hash.

---

## 2. Funciones base reutilizables

Estas funciones son idénticas o casi idénticas entre `simce_downloader.py` y `sige_downloader.py`. Al refactorizar al pipeline formal deben vivir en `scraper/core/` o en una clase base. Por ahora se copian al nuevo scraper y se adaptan.

### `now_iso() → str`
```python
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```
Timestamp ISO 8601 UTC. Usar siempre este helper, nunca `datetime.now()` sin timezone.

---

### `sha256_file(path: Path) → str`
```python
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
```
Lectura en chunks de 64KB. No cargar archivos grandes en memoria. Retorna hex string.

---

### `load_manifest(output_dir: Path) → dict`
```python
def load_manifest(output_dir: Path) -> dict:
    path = output_dir / MANIFEST_FILE
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"created_at": now_iso(), "updated_at": now_iso(), "files": {}}
```
El manifest se crea vacío si no existe. La clave `"files"` es un dict indexado por el identificador único del archivo (UUID para SIMCE, URL para SIGE).

---

### `save_manifest(output_dir: Path, manifest: dict)`
```python
def save_manifest(output_dir: Path, manifest: dict):
    manifest["updated_at"] = now_iso()
    with open(output_dir / MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
```
Siempre actualiza `updated_at`. Llamar por lote (al final de cada categoría / año), no por cada archivo.

---

### `verify_manifest(output_dir: Path)`
```python
def verify_manifest(output_dir: Path):
    manifest = load_manifest(output_dir)
    ok = corrupt = missing = 0
    for key, entry in manifest["files"].items():
        fp = Path(entry.get("filepath", ""))
        if not fp.exists():
            missing += 1; continue
        actual = sha256_file(fp)
        if actual == entry.get("hash", ""):
            ok += 1
        else:
            corrupt += 1
    print(f"OK:{ok}  Corrupt:{corrupt}  Missing:{missing}")
```
Modo `--verify` en todos los scrapers. No descarga nada, solo compara hashes en disco contra manifest.

---

### Estructura de entrada en `manifest["files"]`

**Cuando se descubre (dry-run o fallo previo):**
```json
{
  "uuid_o_url": {
    "url": "...",
    "filename": "...",
    "discovered_at": "2025-...",
    "downloaded": false,
    "error": "descripcion_opcional"
  }
}
```

**Cuando se descarga exitosamente:**
```json
{
  "uuid_o_url": {
    "url": "...",
    "filename": "...",
    "filepath": "/ruta/absoluta/archivo.rar",
    "hash": "sha256hex",
    "size_bytes": 204800,
    "downloaded": true,
    "downloaded_at": "2025-..."
  }
}
```

---

### Lógica de deduplicación (patrón uniforme)

```python
entry = manifest["files"].get(key, {})
if entry.get("hash") and filepath.exists():
    if sha256_file(filepath) == entry["hash"]:
        print(f"  skip: {filename}")
        return "skip"
    # hash mismatch → re-descargar (el archivo cambió en origen)
```

Si el archivo existe pero el hash no coincide: re-descargar. Si no existe aunque el manifest diga que sí: re-descargar.

---

### `safe_filename(name: str, uid: str, ext: str) → str`
```python
def safe_filename(name: str, uid: str, ext: str) -> str:
    name = name or uid[:16]
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "-")
    name = name.strip(". ")
    ext = ext.lstrip(".") or "bin"
    return f"{name}.{ext}"
```
Sanitiza nombres provenientes de APIs o HTML. El fallback es el UID truncado.

---

### Stats al cierre

Todos los scrapers retornan un dict con las mismas claves:
```python
stats = {"ok": 0, "skip": 0, "fail": 0, "empty": 0}
```
- `ok` — descargado exitosamente
- `skip` — hash coincide, sin cambios
- `fail` — error en descarga o respuesta inválida
- `empty` — categoría / año sin archivos

---

### CLI mínima (argparse)

Todo scraper acepta al menos estos argumentos:

```python
parser = argparse.ArgumentParser()
parser.add_argument("--output",  type=Path, default=Path("./data/<fuente>/raw"))
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--verify",  action="store_true")
```

`--dry-run` registra en manifest pero no escribe archivos.  
`--verify` solo valida hashes, sale con código 0.

---

## 3. Tipos de scraper

### Tipo A — REST público sin autenticación
*Ejemplo: `simce_downloader.py`*

```
httpx.Client (sync)
    └── GET /rest/endpoint → JSON con lista de items
            └── por cada item: GET /rest/descarga?uuid={uuid} → binario
```

- Sin estado de sesión.
- `REQUEST_DELAY` entre llamadas para no saturar el servidor.
- Si el endpoint devuelve JSON con distintas estructuras según la categoría, probar varias claves (`data`, `archivos`, `content`, `result`) antes de asumir vacío.

---

### Tipo B — Autenticado: playwright login → httpx descarga
*Ejemplo: `sige_downloader.py`*

```
playwright (browser visible, headless=False)
    └── rellena form + espera captcha manual
    └── espera redirect fuera de /Login
    └── extrae cookies → cierra browser

httpx.Client(cookies=cookies)
    └── POST /endpoint con params → HTML con links PDF
    └── por cada link: GET → binario PDF
```

- El operador interactúa una sola vez (captcha).
- El browser se cierra después del login; todo lo demás es httpx puro.
- Las cookies expiran: si el scraper falla con 302 a /Login, relanzar la fase de login.

---

### Tipo C — Autenticado: ClaveÚnica (OAuth / SAML)
*Pendiente: Trayectorias*

El flujo de autenticación es distinto al de SIGE (que usa credenciales propias). ClaveÚnica es el sistema de identidad estatal chileno y usa un flujo OAuth 2.0 / SAML 2.0 mediado por `https://accounts.claveunica.gob.cl`. Playwright debe:

1. Navegar al portal objetivo.
2. Detectar el botón "Ingresar con ClaveÚnica" y hacer click.
3. Rellenar RUT y clave en el dominio `accounts.claveunica.gob.cl`.
4. Capturar cookies / token tras el redirect de vuelta al portal.

El operador puede necesitar resolver 2FA (clave dinámica por SMS).

---

## 4. Scrapers existentes

### `simce_downloader.py`

| Atributo | Valor |
|---|---|
| Fuente | Agencia de Calidad — `informacionestadistica.agenciaeducacion.cl` |
| Autenticación | Ninguna |
| Tipo | A (REST público) |
| Identificador de archivo | `uuid` del objeto JSON |
| Rango de iteración | Categorías 2–60 (ajustable con `--cat-min` / `--cat-max`) |
| Formato principal | `.rar` (contiene CSVs internos) |
| Output por defecto | `./data/simce/raw/` |

**Argumentos CLI:**
```
--cat-min INT    (default: 2)
--cat-max INT    (default: 60)
--output PATH    (default: ./data/simce/raw)
--dry-run
--all            descarga todos los formatos, no solo RAR
--verify
```

**Notas:**
- La API no siempre declara extensión en el campo `extension`; asumir `.rar` cuando está vacío.
- Si `resolve_download()` falla con todos los patrones de URL: registrar como `"error": "all_patterns_failed"` y continuar.

---

### `sige_downloader.py`

| Atributo | Valor |
|---|---|
| Fuente | SIGE — `sige.mineduc.cl` |
| Autenticación | Credenciales establecimiento (RUT + DV + clave) |
| Tipo | B (playwright login → httpx) |
| Identificador de archivo | URL completa del PDF |
| Rango de iteración | Años 2009–2025 (ajustable con `--years`) |
| Formato | `.pdf` (actas históricas) |
| Output por defecto | `./data/sige/raw/` |
| Credenciales | `SIGE_USER` y `SIGE_PASSWORD` desde `.env` |

**Argumentos CLI:**
```
--years INT [INT ...]    filtrar años específicos
--output PATH            (default: ./data/sige/raw)
--dry-run
--verify
```

**Notas:**
- `SIGE_USER` debe ser el RUT completo con guión (ej: `12345678-9`). El script lo separte en `rut` y `dv`.
- El login abre browser **visible** — el operador debe estar presente para el captcha.
- PDFs menores a 512 bytes se tratan como fallo (respuesta de error del servidor).

---

## 5. Scrapers pendientes

### `datos_abiertos_downloader.py`

| Atributo | Valor |
|---|---|
| Fuente | `datosabiertos.mineduc.cl` |
| Autenticación | Ninguna |
| Tipo esperado | A (REST o HTML scraping) |
| Formatos | CSV + PDF codebook |
| Output | `./data/datos_abiertos/raw/` |

**Lo que hay que investigar antes de implementar:**
- [ ] ¿Expone API REST o es scraping de HTML?
- [ ] ¿Hay paginación o listado completo?
- [ ] Identificador único por archivo (¿URL? ¿slug? ¿versión?)
- [ ] Frecuencia de actualización (para definir el cron)

**Estructura esperada:**
```python
BASE_URL = "https://datosabiertos.mineduc.cl"
# ... fetch_catalog() → lista de datasets disponibles
# ... download_dataset(session, item, output_dir, manifest, dry_run) → str
# ... run(output_dir, dry_run) → None
```

---

### `trayectorias_downloader.py`

| Atributo | Valor |
|---|---|
| Fuente | `trayectorias.mineduc.gob.cl` |
| Autenticación | ClaveÚnica (OAuth / SAML) |
| Tipo esperado | C (playwright ClaveÚnica → httpx o playwright) |
| Credenciales | `TRAYECTORIAS_USER` y `TRAYECTORIAS_PASSWORD` en `.env` |
| Output | `./data/trayectorias/raw/` |

**Lo que hay que investigar antes de implementar:**
- [ ] ¿El portal redirige a `accounts.claveunica.gob.cl` estándar o tiene flujo propio?
- [ ] ¿Qué formato tienen los reportes descargables (CSV, XLSX, PDF)?
- [ ] ¿Hay filtros por RBD o es descarga masiva?
- [ ] ¿La sesión de ClaveÚnica puede reutilizarse entre ejecuciones?

**Diferencia clave respecto a SIGE:**
SIGE tiene login propio en su dominio. Trayectorias usa ClaveÚnica como IdP externo — el flujo playwright debe seguir el redirect a `accounts.claveunica.gob.cl`, autenticarse ahí, y capturar el token/cookies tras el redirect de vuelta.

---

## 6. Checklist para un scraper nuevo

```
[ ] Definir BASE_URL, tipo de acceso (A / B / C) y formato de salida
[ ] Investigar estructura del endpoint o página (¿JSON? ¿HTML? ¿POST?)
[ ] Definir identificador único por archivo (UUID, URL, slug...)
[ ] Copiar funciones base: now_iso, sha256_file, load/save_manifest, verify_manifest, safe_filename
[ ] Implementar fetch_catalog() → list
[ ] Implementar download_item() → "ok" | "skip" | "fail"
    [ ]   dedup check (hash + manifest) al inicio
    [ ]   dry-run branch
    [ ]   registro en manifest tanto en éxito como en fallo
[ ] Implementar run() con stats y checkpoint por lote
[ ] CLI con --output, --dry-run, --verify (+ args específicos de la fuente)
[ ] Agregar SIGE_USER / TRAYECTORIAS_USER al .env.example si requiere credenciales
[ ] Testear con --dry-run antes de la primera descarga real
[ ] Testear --verify después de la primera descarga completa
```