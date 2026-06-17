"""
Artifactory upload helper.

After a test finishes, result files (logs, core dumps, archives) are PUT to
Artifactory under a per-test path and the resulting URL is stored on the
TestExecution row for the dashboard's download link.
"""
from __future__ import annotations

import os

import requests

from backend.config import get_settings
from shared.validation import require_non_empty_str

settings = get_settings()


def upload_artifact(test_id: str, file_path: str) -> str:
    """
    Upload a single artifact file to Artifactory and return its URL.

    Args:
        test_id: Owning test identifier (non-empty); used in the target path.
        file_path: Absolute path to a readable local file to upload.

    Returns:
        str: The fully-qualified Artifactory URL the file was stored at.

    Raises:
        TypeError/ValueError: If ``test_id``/``file_path`` are invalid.
        FileNotFoundError: If ``file_path`` does not exist.
        requests.HTTPError: If Artifactory returns a non-2xx response.
    """
    require_non_empty_str(test_id, "test_id")
    require_non_empty_str(file_path, "file_path")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"artifact not found: {file_path}")

    filename = os.path.basename(file_path)
    url = f"{settings.artifactory_url}/{settings.artifactory_repo}/{test_id}/{filename}"
    with open(file_path, "rb") as fh:
        resp = requests.put(
            url,
            data=fh,
            headers={"Authorization": f"Bearer {settings.artifactory_token}"},
            timeout=120,
        )
    resp.raise_for_status()
    return url