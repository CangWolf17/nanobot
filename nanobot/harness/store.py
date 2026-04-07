from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.harness.models import HarnessRecord, HarnessSnapshot


@dataclass
class HarnessStore:
    workspace_root: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> "HarnessStore":
        return cls(workspace_root=workspace_root)

    @property
    def harnesses_dir(self) -> Path:
        return self.workspace_root / "harnesses"

    @property
    def store_path(self) -> Path:
        return self.harnesses_dir / "store.json"

    def load(self) -> HarnessSnapshot:
        if self.store_path.exists():
            return self._load_store_json()
        snapshot = self._migrate_legacy_workspace_files()
        self.save(snapshot)
        return snapshot

    def save(self, snapshot: HarnessSnapshot) -> None:
        self.harnesses_dir.mkdir(parents=True, exist_ok=True)
        payload = snapshot.to_dict()
        temp_path = self.store_path.with_name(f"{self.store_path.name}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.store_path)

    def _load_store_json(self) -> HarnessSnapshot:
        return HarnessSnapshot.from_dict(json.loads(self.store_path.read_text(encoding="utf-8")))

    def _migrate_legacy_workspace_files(self) -> HarnessSnapshot:
        index_payload = self._read_json_file(self.harnesses_dir / "index.json")
        control_payload = self._read_json_file(self.harnesses_dir / "control.json")
        raw_records = index_payload.get("harnesses") if isinstance(index_payload, dict) else {}

        records: dict[str, HarnessRecord] = {}
        if isinstance(raw_records, dict):
            for harness_id, raw_record in raw_records.items():
                if not isinstance(raw_record, dict):
                    continue
                record_id = str(raw_record.get("id") or harness_id)
                state_payload = self._read_json_file(self.harnesses_dir / record_id / "state.json")
                records[record_id] = HarnessRecord.from_legacy(
                    record_id=record_id,
                    legacy_index=raw_record,
                    legacy_state=state_payload,
                )

        return HarnessSnapshot.from_dict(
            {
                "updated_at": control_payload.get("updated_at"),
                "active_harness_id": control_payload.get("active_harness_id"),
                "records": {record_id: record.to_dict() for record_id, record in records.items()},
            }
        )

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
