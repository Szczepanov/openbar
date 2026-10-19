"""Run the device driver with stub tools; no phone or media is needed."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


BASH = os.environ.get("OPENBAR_TEST_BASH") or (shutil.which("bash") if os.name != "nt" else None)
SUITE = Path(__file__).resolve().parents[1] / "run_device_suite.sh"


@unittest.skipUnless(BASH, "needs bash (on Windows set OPENBAR_TEST_BASH to Git Bash)")
class DeviceSuiteTest(unittest.TestCase):
    def test_parity_mismatch_continues_but_tool_errors_fail_and_stale_runs_are_excluded(self):
        for tool_error in (False, True):
            with self.subTest(tool_error=tool_error), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "bin").mkdir()
                (root / "target/release").mkdir(parents=True)
                (root / "out").mkdir()
                for name in ("getprop", "rustc", "ffmpeg", "sha256sum"):
                    (root / "bin" / name).write_text("#!/bin/bash\necho test\n")
                (root / "target/release/openbar-android-decode").write_text("#!/bin/bash\nexit 0\n")
                (root / "bin/python").write_text('''#!/bin/bash
if [[ $1 == --version ]]; then exit 0; fi
if [[ $2 == summarize ]]; then
  for arg in "${@:3}"; do [[ $arg != *-9.json ]] || exit 9; done
  echo '{}'
  exit 0
fi
if [[ $TOOL_ERROR == 1 ]]; then exit 2; fi
while [[ $# -gt 0 ]]; do
  if [[ $1 == --output ]]; then echo '{"pass": false}' > "$2"; break; fi
  shift
done
exit 1
''')
                for path in [*(root / "bin").iterdir(), root / "target/release/openbar-android-decode"]:
                    path.chmod(0o755)
                # Stale artifacts must neither hide errors nor enter the new repeat summary.
                (root / "out/synthetic-smoke-parity.json").write_text('{"pass": false}')
                if not tool_error:
                    (root / "out/synthetic-smoke-9.json").write_text("{}")
                (root / "suite.sh").write_text(SUITE.read_text().replace("\r\n", "\n"))
                env = dict(os.environ, REPEATS="1", TOOL_ERROR=str(int(tool_error)))
                result = subprocess.run(
                    [BASH, "-c", 'export PATH="$PWD/bin:$PATH"; bash suite.sh repo out'],
                    cwd=root, env=env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode == 0, not tool_error, result.stderr)
                if not tool_error:
                    self.assertTrue((root / "out/back-squat-full-parity.json").is_file())


if __name__ == "__main__":
    unittest.main()
