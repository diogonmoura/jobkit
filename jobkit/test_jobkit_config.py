from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import jobkit_config as cfg

SAMPLE = '''
# comment
[user]
pdf_name = "CV_JaneDoe.pdf"   # trailing comment

[voice]
rules = [
  "No em dashes.",
  'Plain voice, no "buzzwords".',
]

[notify]
channel = "telegram"
telegram_env = "~/secrets/tg.env"

[tracker]
artifact_url = "https://claude.ai/artifact/abc#x"
'''


class TestParseToml(unittest.TestCase):
    def test_subset(self):
        data = cfg.parse_toml(SAMPLE)
        self.assertEqual(data["user"]["pdf_name"], "CV_JaneDoe.pdf")
        self.assertEqual(data["voice"]["rules"], ["No em dashes.", 'Plain voice, no "buzzwords".'])
        self.assertEqual(data["tracker"]["artifact_url"], "https://claude.ai/artifact/abc#x")

    def test_bool_and_int(self):
        self.assertEqual(cfg.parse_toml("a = true\nb = 3\n"), {"a": True, "b": 3})

    def test_bad_line_names_line(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_toml("[user]\nthis is not toml\n")
        self.assertIn("line 2", str(ctx.exception))

    def test_unclosed_array(self):
        with self.assertRaises(cfg.ConfigError):
            cfg.parse_toml('rules = ["a",\n')


class TestLoad(unittest.TestCase):
    def test_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = cfg.load(os.path.join(tmp, "jobkit.toml"))
        self.assertEqual(config.notify_channel, "console")
        self.assertEqual(config.pdf_name, "CV.pdf")

    def test_reads_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "jobkit.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(SAMPLE)
            config = cfg.load(path)
        self.assertEqual(config.pdf_name, "CV_JaneDoe.pdf")
        self.assertEqual(config.notify_channel, "telegram")
        self.assertEqual(config.telegram_env, "~/secrets/tg.env")
        self.assertEqual(len(config.voice_rules), 2)

    def test_unknown_channel_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "jobkit.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('[notify]\nchannel = "slack"\n')
            with self.assertRaises(cfg.ConfigError):
                cfg.load(path)


if __name__ == "__main__":
    unittest.main()


def load_text(text):
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "jobkit.toml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return cfg.load(path)


class TestTrackerBackend(unittest.TestCase):
    def test_backend_defaults_to_artifact_when_only_url_set(self):
        self.assertEqual(load_text('[tracker]\nartifact_url = "https://claude.ai/artifact/x"\n').tracker_backend,
                         "artifact")

    def test_backend_empty_when_nothing_set(self):
        self.assertEqual(load_text("").tracker_backend, "")

    def test_web_fields(self):
        config = load_text('[tracker]\nbackend = "web"\nsupabase_url = "https://ref.supabase.co"\n')
        self.assertEqual((config.tracker_backend, config.supabase_url), ("web", "https://ref.supabase.co"))
        self.assertEqual(config.supabase_env, "~/.jobkit/supabase.env")

    def test_unknown_backend_is_config_error(self):
        with self.assertRaises(cfg.ConfigError):
            load_text('[tracker]\nbackend = "firebase"\n')


class TestSupabaseSecretKey(unittest.TestCase):
    def setUp(self):
        self.env_path = os.path.join(tempfile.mkdtemp(), "supabase.env")
        self.config = cfg.Config(supabase_env=self.env_path)

    def write(self, text):
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_env_var_wins_over_file(self):
        self.write("SUPABASE_SECRET_KEY=from-file\n")
        with mock.patch.dict(os.environ, {"SUPABASE_SECRET_KEY": "from-env"}):
            self.assertEqual(cfg.supabase_secret_key(self.config), "from-env")

    def test_read_from_env_file_with_export_and_quotes(self):
        self.write("# comment\nexport SUPABASE_SECRET_KEY='abc'\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cfg.supabase_secret_key(self.config), "abc")

    def test_missing_is_empty_string(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cfg.supabase_secret_key(self.config), "")


class TestSyncMachine(unittest.TestCase):
    def test_env_wins_then_config_then_hostname(self):
        config = cfg.Config(sync_machine="laptop")
        with mock.patch.dict(os.environ, {"JOBKIT_MACHINE": "github-actions"}):
            self.assertEqual(cfg.sync_machine(config), "github-actions")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cfg.sync_machine(config), "laptop")
            with mock.patch("socket.gethostname", return_value="Mac.local"):
                self.assertEqual(cfg.sync_machine(cfg.Config()), "Mac.local")

    def test_read_from_toml(self):
        self.assertEqual(load_text('[tracker]\nbackend = "web"\nmachine = "laptop"\n').sync_machine, "laptop")
