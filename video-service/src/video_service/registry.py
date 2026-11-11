"""Merge immutable image records with persistent, explicitly managed associations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import CatalogError, VideoCatalog, VideoRecord, _load_calibration
from .state import VideoStateStore
from .youtube import InvalidYouTubeUrl, youtube_video_id


class PersistentVideoRegistry:
    def __init__(self, image_catalog: VideoCatalog, store: VideoStateStore):
        self.image_catalog = image_catalog
        self.store = store

    def _runtime_record(self, video_id: str) -> VideoRecord | None:
        item = self.store.association(video_id)
        if item is None:
            return None
        calibration = _load_calibration(self.store.state_dir, item["calibration_file"], 0)
        if item["synchronization_status"] == "calibrated" and calibration.get("status") != "calibrated":
            raise CatalogError("La calibración persistida no coincide con su estado registrado.")
        return VideoRecord(
            video_id=item["video_id"],
            display_name=f"YouTube · {item['youtube_video_id']}",
            match_ref=item["match_id"],
            origin="youtube",
            source_url=item["source_url"],
            calibration_file=item["calibration_file"],
            synchronization_status=item["synchronization_status"],
            calibration=calibration,
        )

    def list(self) -> list[VideoRecord]:
        static_records = self.image_catalog.list()
        overrides = self.store.overrides()
        static_matches = {record.match_ref for record in static_records}
        result: list[VideoRecord] = []
        for record in static_records:
            if record.match_ref not in overrides:
                result.append(record)
                continue
            active_id = overrides[record.match_ref]
            if active_id is not None:
                runtime = self._runtime_record(active_id)
                if runtime is None:
                    raise CatalogError("La asociación activa no tiene un registro persistente.")
                result.append(runtime)
        for match_id, video_id in overrides.items():
            if match_id in static_matches or video_id is None:
                continue
            runtime = self._runtime_record(video_id)
            if runtime is None:
                raise CatalogError("La asociación activa no tiene un registro persistente.")
            result.append(runtime)
        return sorted(result, key=lambda record: record.video_id)

    def get(self, video_id: str) -> VideoRecord | None:
        return next((record for record in self.list() if record.video_id == video_id), None)

    def for_match(self, match_ref: str) -> VideoRecord | None:
        return next((record for record in self.list() if record.match_ref == match_ref), None)

    def for_youtube_id(self, expected_youtube_id: str) -> VideoRecord | None:
        for record in self.list():
            if record.origin != "youtube" or not record.source_url:
                continue
            try:
                if youtube_video_id(record.source_url) == expected_youtube_id:
                    return record
            except InvalidYouTubeUrl:
                continue
        return None
