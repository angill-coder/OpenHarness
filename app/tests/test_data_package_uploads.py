# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from data_package_uploads import (  # noqa: E402
    DataPackageUploadError,
    DataPackageUploadService,
)


def _pptx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:t>%s</a:t></p:sld>'
            % ("真实项目参考报告正文。" * 20),
        )
    return output.getvalue()


class DataPackageUploadServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = DataPackageUploadService(
            self.root / "uploads",
            max_files=20,
            max_total_bytes=10 * 1024 * 1024,
            max_file_bytes=5 * 1024 * 1024,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _put(self, upload_id, path, content):
        return self.service.upload_file(
            upload_id,
            "session-a",
            path,
            io.BytesIO(content),
            len(content),
        )

    def test_folder_upload_builds_unified_dataset(self):
        upload = self.service.start(
            "session-a",
            "真实项目包",
            "folder",
        )
        self._put(
            upload.upload_id,
            "真实项目包/001_AI效率/source/interview.md",
            "访谈素材".encode("utf-8"),
        )
        self._put(
            upload.upload_id,
            "真实项目包/001_AI效率/reference.pptx",
            _pptx_bytes(),
        )

        completed, count = self.service.finalize(
            upload.upload_id,
            "session-a",
        )

        self.assertEqual(count, 1)
        self.assertEqual(completed.status, "completed")
        payload = json.loads(
            Path(completed.dataset_path).read_text(encoding="utf-8")
        )
        self.assertEqual(payload["schema_version"], "openharness-wb/v1")
        self.assertEqual(payload["cases"][0]["case_id"], "rr-upload-001")
        self.assertEqual(payload["cases"][0]["input"]["topic"], "AI效率")
        source = (
            Path(completed.dataset_path).parent
            / payload["cases"][0]["input_files"][0]["source"]
        ).resolve()
        self.assertTrue(source.is_dir())

    def test_zip_traversal_is_rejected(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("../escape.txt", "bad")
        content = archive_bytes.getvalue()
        upload = self.service.start("session-a", "bad.zip", "zip")
        self._put(upload.upload_id, "bad.zip", content)

        with self.assertRaisesRegex(DataPackageUploadError, "非法上传路径"):
            self.service.finalize(upload.upload_id, "session-a")

        self.assertFalse((self.root / "escape.txt").exists())

    def test_upload_path_traversal_and_duplicate_are_rejected(self):
        upload = self.service.start("session-a", "folder", "folder")
        with self.assertRaisesRegex(DataPackageUploadError, "非法上传路径"):
            self._put(upload.upload_id, "../escape.txt", b"bad")
        self._put(upload.upload_id, "folder/file.txt", b"ok")
        with self.assertRaisesRegex(DataPackageUploadError, "重复路径"):
            self._put(upload.upload_id, "folder/file.txt", b"again")


if __name__ == "__main__":
    unittest.main()
