"""One-worker video processing jobs; downloaded media stays under one temp root."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Any, Callable
import uuid

from .catalog import _load_calibration
from .downloader import DEFAULT_MAX_BYTES, DownloadError, download_youtube_video
from .state import VideoStateStore
from .youtube import youtube_video_id


MAX_DURATION_SECONDS = 3 * 60 * 60
MIN_FREE_SPACE_MARGIN_BYTES = 512 * 1024 * 1024
JOB_DIRECTORY_RE = re.compile(r"^job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
logger = logging.getLogger(__name__)


class VideoJobManager:
    def __init__(self, store: VideoStateStore, temp_dir: Path, *,
                 downloader: Callable[..., Path] | None = None,
                 calibrator: Callable[..., dict[str, Any]] | None = None,
                 probe: Callable[[Path], dict[str, Any]] | None = None,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 max_duration_seconds: int = MAX_DURATION_SECONDS):
        self.store = store
        self.temp_dir = Path(temp_dir).expanduser().resolve()
        self.downloader = downloader or download_youtube_video
        self.calibrator = calibrator
        self.probe = probe
        self.max_bytes = max_bytes
        self.max_duration_seconds = max_duration_seconds
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-job")

    def prepare(self) -> None:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.store.recover_interrupted_jobs()
        self.store.cleanup_calibration_temps()
        self.cleanup_orphans()

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def cleanup_orphans(self) -> None:
        root = self.temp_dir.resolve()
        if not root.is_dir():
            return
        for child in root.iterdir():
            if not JOB_DIRECTORY_RE.fullmatch(child.name) or child.is_symlink() or not child.is_dir():
                continue
            resolved = child.resolve()
            if resolved.parent == root:
                shutil.rmtree(resolved, ignore_errors=True)

    def enqueue(self, match_ref: str, source_url: str, sports_events: dict[str, Any] | None,
                *, max_active_jobs: int = 2) -> dict[str, Any]:
        video_id = youtube_video_id(source_url)
        # Retain the canonical public URL only; tracking/query values supplied
        # in a share link are not needed for download or persistent metadata.
        source_url = f"https://www.youtube.com/watch?v={video_id}"
        job_id = str(uuid.uuid4())
        self.store.create_job(job_id, match_ref, video_id, source_url, sports_events,
                              max_active_jobs=max_active_jobs)
        self.executor.submit(self._process, job_id)
        result = self.store.get_job(job_id)
        if result is None:
            raise RuntimeError("No se pudo recuperar el trabajo recién creado.")
        return result

    def _load_calibrator(self):
        if self.calibrator is not None:
            return self.calibrator
        from video_sync.auto_calibrate import run_auto_calibration
        return run_auto_calibration

    def _load_probe(self):
        if self.probe is not None:
            return self.probe
        from video_sync.media import probe
        return probe

    def _work_dir(self, job_id: str) -> Path:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError("Identificador de trabajo no válido.")
        root = self.temp_dir.resolve()
        path = (root / f"job-{job_id}").resolve()
        if path.parent != root:
            raise ValueError("La ruta de trabajo no es segura.")
        return path

    def _remove_work_dir(self, work_dir: Path, job_id: str) -> None:
        root = self.temp_dir.resolve()
        if work_dir.parent != root or work_dir.name != f"job-{job_id}":
            raise ValueError("La ruta temporal del trabajo no es segura.")
        if work_dir.exists():
            shutil.rmtree(work_dir)
        if work_dir.exists():
            raise OSError("No se pudo limpiar el directorio temporal del trabajo.")

    def _progress(self, job_id: str, status: str, message: str) -> None:
        self.store.update_job(job_id, status=status, message=message)

    def _process(self, job_id: str) -> None:
        work_dir = self._work_dir(job_id)
        try:
            persisted_input = self.store.processing_input(job_id)
            if persisted_input is None:
                raise ValueError("El trabajo no contiene su entrada persistida.")
            match_ref = persisted_input["match_ref"]
            source_url = persisted_input["source_url"]
            sports_events = persisted_input["sports_events"]
            youtube_id = youtube_video_id(source_url)
            self._progress(job_id, "downloading", "Descargando la versión actual del vídeo de YouTube.")
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.temp_dir).free < self.max_bytes + MIN_FREE_SPACE_MARGIN_BYTES:
                raise DownloadError(
                    "Video Service no tiene espacio temporal suficiente para descargar el vídeo.",
                    error_code="storage_error",
                    technical_type="InsufficientTemporarySpace",
                )
            video_path = self.downloader(source_url, youtube_id, work_dir, max_bytes=self.max_bytes)
            resolved_video = Path(video_path).resolve()
            if not resolved_video.is_relative_to(work_dir.resolve()) or not resolved_video.is_file():
                raise DownloadError("La descarga no produjo un archivo dentro del directorio temporal del trabajo.")
            size = resolved_video.stat().st_size
            if size <= 0 or size > self.max_bytes:
                raise DownloadError("El archivo descargado está vacío o supera el tamaño máximo permitido.")

            video_info = self._load_probe()(resolved_video)
            duration = video_info.get("duration_seconds")
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
                raise ValueError("No se pudo verificar la duración del vídeo.")
            if duration > self.max_duration_seconds:
                raise DownloadError("El vídeo supera la duración máxima permitida de tres horas.")
            self._progress(job_id, "calibrating", "Analizando el reloj y la sincronización con OCR V5.1.")

            events_status = "used" if sports_events is not None and sports_events.get("events") else "not_available"
            calibration_path = work_dir / "calibration.json"
            report_dir = work_dir / "ocr-report"
            report = self._load_calibrator()(
                resolved_video,
                output_path=calibration_path,
                report_dir=report_dir,
                sports_events=sports_events,
                sports_events_source=("dashboard-bff:video-sync.sports-events/v1"
                                      if events_status == "used" else None),
            )
            if not isinstance(report, dict) or not calibration_path.is_file():
                raise ValueError("El motor OCR no dejó una calibración verificable.")
            try:
                persisted_report = json.loads(calibration_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("El resultado OCR no es un JSON persistible válido.") from exc
            if not isinstance(persisted_report, dict) or not isinstance(persisted_report.get("segments"), list):
                raise ValueError("El resultado OCR no contiene los segmentos esperados.")
            if persisted_report.get("status") != report.get("status"):
                raise ValueError("El informe devuelto y el JSON OCR no coinciden.")

            self._progress(job_id, "saving", "Verificando y guardando la calibración persistente.")
            processed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            persisted_report.setdefault("video", {})["path"] = None
            for collection in ("detector_passes", "region_events"):
                for item in persisted_report.get(collection, []):
                    if isinstance(item, dict):
                        item["report_json"] = None
                        item["report_image"] = None
            if persisted_report.get("sports_events", {}).get("source"):
                persisted_report["sports_events"]["source"] = "dashboard-bff:video-sync.sports-events/v1"
            persisted_report["processing_metadata"] = {
                "job_id": job_id,
                "match_ref": match_ref,
                "youtube_video_id": youtube_id,
                "source_url": source_url,
                "processed_at": processed_at,
                "downloaded_file_bytes": size,
                "duration_seconds": round(float(duration), 3),
                "sports_events_status": events_status,
            }
            persisted_report["video"]["path"] = None
            calibration_path.write_text(
                json.dumps(persisted_report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            # Validate the sanitized persisted form with the same loader the
            # public catalogue uses, before publishing any active association.
            persisted_report = _load_calibration(work_dir, "calibration.json", 0)
            video_id = f"youtube-{job_id}"
            sync_status = "calibrated" if persisted_report.get("status") == "calibrated" else "uncertain"
            evidence_message = ("Se incorporaron los eventos deportivos recibidos como apoyo a la calibración."
                                if events_status == "used" else
                                "No se recibieron eventos deportivos utilizables; la calibración se realizó solo con OCR.")
            result_message = ("Vídeo y sincronización OCR guardados."
                              if sync_status == "calibrated" else
                              "Vídeo guardado, pero el OCR no confirmó todos los periodos; los saltos quedan sujetos a cobertura.")
            message = f"{result_message} {evidence_message} El MP4 temporal se eliminó."
            metadata = {
                "processed_at": processed_at,
                "duration_seconds": round(float(duration), 3),
                "file_bytes": size,
                "width": video_info.get("width"),
                "height": video_info.get("height"),
                "codec": video_info.get("codec"),
                "sports_events_status": events_status,
            }
            # Do not publish an active association or report success while a
            # large source file remains in the temporary storage. The OCR
            # result is already in memory and the persistent JSON is written
            # immediately after this verified cleanup.
            self._remove_work_dir(work_dir, job_id)
            self.store.save_completed(
                job_id=job_id,
                match_id=match_ref,
                video_id=video_id,
                youtube_video_id=youtube_id,
                source_url=source_url,
                calibration=persisted_report,
                synchronization_status=sync_status,
                metadata=metadata,
                message=message,
            )
        except DownloadError as exc:
            logger.warning("Video download failed job=%s category=%s exception=%s exit_code=%s",
                           job_id, exc.error_code, exc.technical_type or "VideoServiceDownloadError", exc.exit_code)
            self.store.update_job(job_id, status="failed", message=str(exc), error_code=exc.error_code)
        except OSError as exc:
            logger.warning("Video storage failed job=%s exception=%s errno=%s",
                           job_id, type(exc).__name__, exc.errno)
            self.store.update_job(
                job_id,
                status="failed",
                message="Video Service no pudo escribir en su almacenamiento temporal. Comprueba el espacio y los permisos.",
                error_code="storage_error",
            )
        except Exception:
            logger.exception("Video processing failed for job %s", job_id)
            self.store.update_job(
                job_id,
                status="failed",
                message="El procesamiento OCR no pudo completarse. Comprueba el vídeo e inténtalo de nuevo.",
                error_code="processing_failed",
            )
        finally:
            try:
                self._remove_work_dir(work_dir, job_id)
            except OSError:
                # The persistent job remains truthful; startup cleanup retries
                # only these UUID-owned job folders.
                logger.exception("Could not clean temporary files for job %s", job_id)
