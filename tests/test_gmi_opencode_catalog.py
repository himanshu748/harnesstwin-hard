from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
import gmi_opencode_catalog as catalog

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"data": [
            {"id":"zai-org/GLM-5.3","name":"GLM 5.3","context_length":262144},
            {"id":"openai/gpt-6-sol","pricing":{"prompt":"0.0000003"}}
        ]}

    def test_model_ids_are_preserved_and_secrets_excluded(self):
        output = catalog.build_custom_provider(self.payload)
        provider = output['providers']['gmi-live']
        self.assertEqual(provider['models']['zai-org/GLM-5.3']['modelID'], 'zai-org/GLM-5.3')
        self.assertEqual(provider['models']['zai-org/GLM-5.3']['limit']['context'], 262144)
        self.assertNotIn('limit',provider['models']['openai/gpt-6-sol'])
        self.assertNotIn('apiKey',json.dumps(output))
        self.assertEqual(provider['env'],['GMICLOUD_API_KEY','GMI_API_KEY'])

    def test_no_false_capabilities(self):
        models = catalog.build_custom_provider(self.payload)['providers']['gmi-live']['models']
        for config in models.values():
            self.assertNotIn('capabilities',config)
            self.assertNotIn('modalities',config)

    def test_rejects_duplicate_or_empty_inventory(self):
        with self.assertRaises(ValueError):catalog.validate_models({"data":[]})
        with self.assertRaises(ValueError):catalog.validate_models({"data":self.payload['data']*2})

    def test_merge_preserves_users_config(self):
        custom = catalog.build_custom_provider(self.payload)
        base = {"theme":"zen", "providers":{"other":{"model":"x"}}}
        result = catalog.merge_config(base, custom)
        self.assertEqual(result['providers']['other'],{"model":"x"})
        self.assertEqual(result['theme'],'zen')

    def test_cli_offline_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            in_ = Path(d)/'models.json';out=Path(d)/'opencode.json'
            in_.write_text(json.dumps(self.payload))
            self.assertEqual(catalog.main(['--inventory',str(in_), '--out',str(out)]),0)
            self.assertIn('gmi-live',out.read_text())

if __name__=='__main__':unittest.main()
