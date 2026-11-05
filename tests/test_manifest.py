import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from validate_manifest import validate

SAMPLE=Path(__file__).resolve().parents[1]/'examples/change-manifest.json'

class ManifestValidationTests(unittest.TestCase):
    def setUp(self):
        self.m=json.loads(SAMPLE.read_text())

    def test_valid_structure(self):
        self.assertEqual(validate(self.m),[])

    def test_invalid_revision_is_rejected(self):
        self.m['repositories'][0]['revision']='main'
        self.assertTrue(validate(self.m))

    def test_non_allowlisted_repository_rejected(self):
        self.m['repositories'][0]['name']='ediscovery-backend'
        self.assertTrue(validate(self.m))

    def test_duplicate_repository_rejected(self):
        self.m['repositories'][1]['name']=self.m['repositories'][0]['name']
        self.assertTrue(validate(self.m))

    def test_production_approval_required(self):
        self.m['production_approval_required']=False
        self.assertTrue(validate(self.m))

    def test_unknown_fields_rejected(self):
        self.m['auto_merge']=True
        self.assertTrue(validate(self.m))

if __name__=='__main__': unittest.main()
