"""Fresh-process regressions for dotenv-only collector/loader data paths."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SERVICE_ROOT = Path(__file__).resolve().parents[1]


class DataRootInitializationTests(unittest.TestCase):
    def _check_roots(self, *, relative=False, process_override=False):
        # Copy the entry points and their real local dependencies so the test
        # exercises a service-local .env without touching developer credentials.
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            service = workspace / "services" / "recommendation-api"
            for directory, filenames in {
                "recommendation": ["__init__.py", "env.py", "paths.py", "serving_db.py"],
                "scripts": ["supplement_serving_tables.py", "ingest_vacancy_rate.py", "rone_api.py"],
            }.items():
                (service / directory).mkdir(parents=True)
                for filename in filenames:
                    shutil.copy2(SERVICE_ROOT / directory / filename, service / directory / filename)
            configured = "mounted data" if relative else str(workspace / "mounted data")
            (service / ".env").write_text(
                f'RECOMMENDATION_DATA_ROOT="{configured}"\n', encoding="utf-8"
            )
            expected = (service / configured).resolve() if relative else Path(configured)
            env = os.environ.copy()
            for key in ("RECOMMENDATION_DATA_ROOT", "RECOMMENDATION_API_ENV_FILE", "PYTHONPATH"):
                env.pop(key, None)
            if process_override:
                expected = workspace / "exported data"
                env["RECOMMENDATION_DATA_ROOT"] = str(expected)
            expected.mkdir(parents=True)
            env["PYTHONPATH"] = os.pathsep.join([str(service), str(service / "scripts")])
            probes = {
                "supplement_serving_tables": "[str(module.ROOT), str(module.serving_db.ROOT)]",
                "ingest_vacancy_rate": "[module.ROOT, str(Path(module.OUT).parents[2]), str(Path(module.MANIFEST).parents[2])]",
            }
            for name, expression in probes.items():
                with self.subTest(script=name):
                    result = subprocess.run(
                        [sys.executable, "-c", f"import json; from pathlib import Path; import {name} as module; print(json.dumps({expression}))"],
                        cwd=workspace, env=env, capture_output=True, text=True, check=True,
                    )
                    roots = json.loads(result.stdout)
                    self.assertTrue(roots)
                    self.assertEqual(roots, [str(expected)] * len(roots))

    def test_service_dotenv_selects_external_data_root_before_imports(self):
        self._check_roots()

    def test_relative_dotenv_root_resolves_from_service_directory(self):
        self._check_roots(relative=True)

    def test_exported_data_root_takes_precedence_over_service_dotenv(self):
        self._check_roots(process_override=True)


if __name__ == "__main__":
    unittest.main()
