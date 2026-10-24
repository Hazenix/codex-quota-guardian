import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ModelCatalogTest(unittest.TestCase):
    def test_catalog_contains_only_supported_deepseek_models(self):
        catalog = json.loads(
            (PROJECT_ROOT / "resources" / "models.json").read_text(encoding="utf-8")
        )

        models = catalog["models"]
        self.assertEqual(
            {"deepseek-flash", "deepseek-v4-pro"},
            {model["slug"] for model in models},
        )
        self.assertEqual(2, len(models))
        for model in models:
            self.assertEqual("0.144.0", model["minimal_client_version"])
            self.assertTrue(model["supported_in_api"])
            self.assertTrue(model["base_instructions"])


if __name__ == "__main__":
    unittest.main()
