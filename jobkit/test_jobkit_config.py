from __future__ import annotations

import os
import tempfile
import unittest

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
