from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "hermes" / "bootstrap-profiles.sh"


class HermesBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile_root = self.root / "profiles"
        self.config_root = self.root / "config"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.trace = self.root / "commands.log"
        hermes_stub = self.bin_dir / "hermes"
        hermes_stub.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = profile ] && [ \"$2\" = create ]; then\n"
            "  mkdir -p \"$HERMES_PROFILE_ROOT/$3\"\n"
            "  : > \"$HERMES_PROFILE_ROOT/$3/SOUL.md\"\n"
            "  : > \"$HERMES_PROFILE_ROOT/$3/.env\"\n"
            "  printf 'create:%s\\n' \"$3\" >> \"$BOOTSTRAP_TRACE\"\n"
            "elif [ \"$1\" = gateway ] && [ \"$2\" = run ]; then\n"
            "  printf 'gateway\\n' >> \"$BOOTSTRAP_TRACE\"\n"
            "else exit 9; fi\n"
        )
        hermes_stub.chmod(0o755)
        self.log_config = self.config_root / "noc-log-analysis" / "config.yaml"
        self.daily_config = self.config_root / "noc-daily-report" / "config.yaml"
        self.log_config.parent.mkdir(parents=True)
        self.log_config.write_text("log-policy: restricted\n")
        self.daily_config.parent.mkdir(parents=True)
        self.daily_config.write_text("daily-policy: restricted\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_bootstrap(self, daily: str = "false") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env['PATH']}",
                "HERMES_PROFILE_ROOT": str(self.profile_root),
                "NOC_REPORT_HERMES_CONFIG_ROOT": str(self.config_root),
                "DAILY_REPORT_AI_ENABLED": daily,
                "API_SERVER_KEY": "temporary-test-key",
                "BOOTSTRAP_TRACE": str(self.trace),
            }
        )
        return subprocess.run(
            ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, check=False
        )

    def test_disabled_daily_profile_is_not_required_or_touched(self) -> None:
        self.daily_config.unlink()

        result = self.run_bootstrap()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.profile_root / "noc-log-analysis/config.yaml").exists())
        self.assertFalse((self.profile_root / "noc-daily-report").exists())
        self.assertEqual(self.trace.read_text().splitlines(), ["create:noc-log-analysis", "gateway"])
        self.assertIn("disabled; bootstrap skipped", result.stdout)

    def test_enabled_daily_bootstrap_failure_is_visible_but_keeps_gateway_up(self) -> None:
        self.daily_config.unlink()

        result = self.run_bootstrap("true")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Daily Report profile bootstrap failed", result.stderr)
        self.assertTrue((self.profile_root / "noc-log-analysis/config.yaml").exists())
        self.assertEqual(
            self.trace.read_text().splitlines(),
            ["create:noc-log-analysis", "create:noc-daily-report", "gateway"],
        )

    def test_both_profiles_bootstrap_when_daily_is_enabled(self) -> None:
        result = self.run_bootstrap("true")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.profile_root / "noc-log-analysis/config.yaml").exists())
        self.assertTrue((self.profile_root / "noc-daily-report/config.yaml").exists())
        self.assertEqual(
            self.trace.read_text().splitlines(),
            ["create:noc-log-analysis", "create:noc-daily-report", "gateway"],
        )

    def test_invalid_daily_gate_fails_closed(self) -> None:
        result = self.run_bootstrap("yes")

        self.assertEqual(result.returncode, 2)
        self.assertIn("must be exactly true or false", result.stderr)
        self.assertFalse(self.trace.exists())

    def test_missing_required_log_policy_prevents_gateway_start(self) -> None:
        self.log_config.unlink()

        result = self.run_bootstrap()

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("gateway", self.trace.read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
