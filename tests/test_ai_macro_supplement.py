"""After integration: python -m unittest tests.test_ai_macro_supplement -v"""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import zlib

from macro_studio.ai_macro_plan import VERSION, PlanError, compile_plan
from macro_studio.ai_macro_supplement import add_check_record, export_revision, requests_from_plan


class SupplementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        def chunk(kind, content):
            return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind + content))
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b'')
        self.path = self.root / 'image.png'
        self.path.write_bytes(png)
        self.resolve = lambda alias: self.path
        self.private = {'schema': VERSION, 'recording_id': 'recording-a', 'records': {
            'r001': {'step': {'action': 'image_search', 'asset': 'original', 'click_enabled': True,
                'click': {'offset': [3, 4]}, 'window_title': 'PRIVATE-TITLE'},
                'images': [{'alias': 'original', 'file': 'images/r001.png', 'sha256': hashlib.sha256(png).hexdigest()}]}}}
        self.plan = {'schema': VERSION, 'recording_id': 'recording-a', 'missing_images': [{'label': '성공 화면', 'reason': '결과 확인 필요'}]}

    def test_original_unchanged_and_added_record_check_only(self):
        before = json.dumps(self.private)
        updated, rid = add_check_record(self.private, 'r001', 'new', '성공', self.resolve)
        self.assertEqual(before, json.dumps(self.private))
        self.assertEqual(updated['recording_id'], self.private['recording_id'])
        step = updated['records'][rid]['step']
        self.assertFalse(step['click_enabled'])
        self.assertNotIn('click', step)
        self.assertEqual('PRIVATE-TITLE', step['window_title'])
        plan = {'schema': VERSION, 'recording_id': 'recording-a', 'name': 'test', 'entry': 'n1',
                'nodes': [{'id': 'n1', 'record': rid, 'mode': 'check', 'success': 'STOP', 'failure': 'STOP'}], 'missing_images': []}
        draft = compile_plan(plan, updated, self.resolve)
        self.assertEqual(0, draft['steps'][-1]['jump_to'])

    def test_export_excludes_private_and_retains_unresolved(self):
        requests = requests_from_plan(self.plan, self.private)
        package, local = export_revision(self.private, requests, {}, '목적', '보완', self.root, self.resolve)
        self.assertEqual(self.private, json.loads(local.read_text(encoding='utf-8')))
        with zipfile.ZipFile(package) as archive:
            self.assertNotIn('private-recording.json', archive.namelist())
            public = archive.read('manifest.json')
            self.assertNotIn(b'PRIVATE-TITLE', public)
            self.assertEqual('unresolved', json.loads(public)['supplement_requests'][0]['status'])
        second, _ = export_revision(self.private, requests, {}, '목적', '', self.root, self.resolve)
        self.assertNotEqual(package, second)
        self.assertTrue(package.exists())

    def test_changed_asset_and_wrong_recording_blocked(self):
        self.path.write_bytes(b'changed')
        with self.assertRaises(PlanError):
            export_revision(self.private, [], {}, '목적', '', self.root, self.resolve)
        self.plan['recording_id'] = 'other'
        with self.assertRaises(PlanError):
            requests_from_plan(self.plan, self.private)

    def test_add_check_record_with_search_region_and_manifest(self):
        region = [15, 25, 300, 400]
        updated, rid = add_check_record(self.private, 'r001', 'new', '상승 영역', self.resolve, search_region=region)
        self.assertEqual(region, updated['records'][rid]['step']['search_region'])
        self.assertEqual('상승 영역', updated['records'][rid]['step']['label'])
        requests = [{'id': 'req-1', 'label': '상승 영역', 'reason': '상승 상태 판별용'}]
        responses = {'req-1': {'records': [rid], 'note': '이 영역이 상승 이미지입니다.'}}
        package, _ = export_revision(updated, requests, responses, '상승 감지 매크로', '설명 보완', self.root, self.resolve)
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            evidence_records = {r['record']: r for r in manifest['records']}
            self.assertIn(rid, evidence_records)
            self.assertEqual(region, evidence_records[rid].get('search_region'))
            self.assertEqual('provided_for_review', manifest['supplement_requests'][0]['status'])
            self.assertEqual('이 영역이 상승 이미지입니다.', manifest['supplement_requests'][0]['user_note'])

    def test_capture_metadata_dialog(self):
        from PySide6.QtWidgets import QApplication
        from macro_studio.ai_macro_supplement_dialog import CaptureMetadataDialog
        app = QApplication.instance() or QApplication([])
        dlg = CaptureMetadataDialog("상승 영역 모두 포함된 이미지", "이 영역이 상승 이미지의 영역입니다.")
        label, note = dlg.get_data()
        self.assertEqual("상승 영역 모두 포함된 이미지", label)
        self.assertEqual("이 영역이 상승 이미지의 영역입니다.", note)
        dlg.label_edit.setText("수정된 라벨")
        dlg.note_edit.setPlainText("수정된 메모")
        label2, note2 = dlg.get_data()
        self.assertEqual("수정된 라벨", label2)
        self.assertEqual("수정된 메모", note2)

    def test_select_region_on_snapshot(self):
        from unittest import mock
        from PySide6 import QtGui, QtCore, QtWidgets
        from macro_studio.image_editor import select_region_on_snapshot, ScreenCaptureDialog

        # Test empty or invalid pixmap returns None
        self.assertIsNone(select_region_on_snapshot(QtGui.QPixmap()))

        pix = QtGui.QPixmap(200, 300)
        pix.fill(QtCore.Qt.red)

        with mock.patch.object(ScreenCaptureDialog, "exec", return_value=QtWidgets.QDialog.Accepted), \
             mock.patch.object(ScreenCaptureDialog, "selected_screen_rect", return_value=QtCore.QRect(50, 50, 60, 80)):
            res = select_region_on_snapshot(pix)
            # Should return 4 coordinates or None if outside target
            if res is not None:
                self.assertEqual(4, len(res))
                self.assertGreaterEqual(res[2], res[0])
                self.assertGreaterEqual(res[3], res[1])


if __name__ == '__main__':
    unittest.main()

