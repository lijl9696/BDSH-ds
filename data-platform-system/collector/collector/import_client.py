from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx


class ReportWebClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password)

    def import_file(
        self,
        *,
        platform_code: str,
        file_path: Path,
        period_start: date,
        period_end: date,
        duplicate_policy: str = "skip",
        date_field: str | None = None,
        store_code_field: str | None = None,
        store_name_field: str | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(auth=self.auth, timeout=120) as client:
            with file_path.open("rb") as file_obj:
                upload = client.post(
                    f"{self.base_url}/imports/files",
                    data={
                        "platform_code": platform_code,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "duplicate_policy": duplicate_policy,
                        "date_field": date_field or "",
                        "store_code_field": store_code_field or "",
                        "store_name_field": store_name_field or "",
                    },
                    files={"file": (file_path.name, file_obj)},
                )
            if upload.status_code == 409:
                return {"status": "duplicate", "detail": upload.json().get("detail")}
            upload.raise_for_status()
            batch_id = upload.json()["batch_id"]

            preview = client.get(f"{self.base_url}/imports/{batch_id}/preview")
            preview.raise_for_status()

            commit = client.post(f"{self.base_url}/imports/{batch_id}/commit")
            commit.raise_for_status()
            return {"status": "imported", "batch_id": batch_id, "preview": preview.json(), "commit": commit.json()}
