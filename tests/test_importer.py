import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.getcwd())

from ezsway.core.importer import parse_kanshi_config, parse_locations_conf, resolve_to_profile_outputs
from ezsway.core.wm_adapter import Monitor

from tests.test_profile_manager import make_monitor


class TestParseLocationsConf(unittest.TestCase):
    def _write(self, text):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return Path(f.name)

    def test_block_form_quoted_descriptor(self):
        path = self._write('''
# Top Center - 24" Dell C2422HE
output "Dell Inc. DELL C2422HE 802H193" {
    mode 1920x1080@60Hz
    position 1267,0
    scale 1.2
    dpms on
}
''')
        parsed = parse_locations_conf(path)
        self.assertEqual(len(parsed), 1)
        p = parsed[0]
        self.assertEqual(p.criteria, "Dell Inc. DELL C2422HE 802H193")
        self.assertEqual(p.mode, "1920x1080@60Hz")
        self.assertEqual(p.position, "1267 0")
        self.assertEqual(p.scale, 1.2)
        self.assertTrue(p.enabled)

    def test_block_form_transform_and_no_hz(self):
        path = self._write('''
output "AU Optronics 0x499A Unknown" {
    mode 2560x1600
    position 3948,181
    scale 1.0
    dpms on
}
output "Dell Inc. DELL P2422H 2V2KGF3" {
    mode 1920x1080@60Hz
    position 2868,181
    scale 1.0
    transform 90
    dpms on
}
''')
        parsed = parse_locations_conf(path)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].mode, "2560x1600")
        self.assertIsNone(parsed[0].transform)
        self.assertEqual(parsed[1].transform, "90")

    def test_singleline_connector_blanket_rule(self):
        """Regression test for allison_wide.conf's dialect: bare connector
        name, single line, comma position -- distinct from the quoted
        block-form used everywhere else."""
        path = self._write(
            "output DP-1 mode 1920x1080@60Hz position 0,0 scale 1\n"
            "output DP-2 mode 1920x1080@60Hz position 0,0 scale 1\n"
        )
        parsed = parse_locations_conf(path)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].criteria, "DP-1")
        self.assertEqual(parsed[0].position, "0 0")

    def test_comments_and_blank_lines_ignored(self):
        path = self._write('''
### 2UWxdell_office ###
# LG ULTRAWIDE 501NTYT26154 (Rightmost)

output "LG Electronics LG ULTRAWIDE 501NTYT26154" {
    mode 3440x1440@100Hz
    position 5360,0
    scale 1.0
}
''')
        parsed = parse_locations_conf(path)
        self.assertEqual(len(parsed), 1)


class TestParseKanshiConfig(unittest.TestCase):
    def _write(self, text):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return Path(f.name)

    def test_named_profile_parsed(self):
        path = self._write('''
profile both_external {
    output DP-6 position 4480,520 mode 1920x1080@60.0000 scale 1.00 transform normal
    output DP-4 position 2560,520 mode 1920x1080@60.0000 scale 1.00 transform normal
    output eDP-1 position 0,0 mode 2560x1600@60.0390 scale 1.00 transform normal
}
''')
        profiles = parse_kanshi_config(path)
        self.assertIn("both_external", profiles)
        self.assertEqual(len(profiles["both_external"]), 3)
        dp6 = next(o for o in profiles["both_external"] if o.criteria == "DP-6")
        self.assertEqual(dp6.position, "4480 520")
        self.assertEqual(dp6.mode, "1920x1080@60.0000")

    def test_disable_directive_parsed(self):
        path = self._write('''
profile dp4_only {
    output eDP-1 disable
    output DP-4 mode 3840x2160@30.000Hz position 0,0 scale 1.4
}
''')
        profiles = parse_kanshi_config(path)
        edp1 = next(o for o in profiles["dp4_only"] if o.criteria == "eDP-1")
        self.assertFalse(edp1.enabled)

    def test_anonymous_profiles_skipped(self):
        """Regression test for the garbage-data shape found in a real
        abandoned kanshi config: unnamed `profile { ... }` blocks have no
        label to import under and must not surface as importable."""
        path = self._write('''
profile named_one {
    output eDP-1 position 0,0 mode 2560x1600@60.0390 scale 1.00 transform normal
}
profile {
    output DP-3 position 2560,0 mode 640x480@59.9400 scale 1.00 transform normal
}
''')
        profiles = parse_kanshi_config(path)
        self.assertEqual(list(profiles.keys()), ["named_one"])


class TestResolveToProfileOutputs(unittest.TestCase):
    def test_resolves_by_quoted_descriptor(self):
        live = [make_monitor(name="DP-1", make="Dell Inc.", model="DELL C2422HE", serial="802H193")]
        from ezsway.core.importer import ParsedOutput
        parsed = [ParsedOutput(criteria="Dell Inc. DELL C2422HE 802H193", mode="1920x1080@60Hz",
                                position="1267 0", scale=1.2)]
        outputs, unresolved = resolve_to_profile_outputs(parsed, live)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["unique_id"], live[0].unique_id)
        self.assertEqual(outputs[0]["scale"], 1.2)
        self.assertEqual(unresolved, [])

    def test_resolves_by_bare_connector_name(self):
        live = [make_monitor(name="eDP-1")]
        from ezsway.core.importer import ParsedOutput
        parsed = [ParsedOutput(criteria="eDP-1", mode="2560x1600", position="0 0")]
        outputs, unresolved = resolve_to_profile_outputs(parsed, live)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["unique_id"], live[0].unique_id)

    def test_unresolved_when_hardware_not_connected(self):
        """A descriptor for hardware that isn't currently plugged in can't
        be converted to a unique_id -- it must be reported, not guessed."""
        from ezsway.core.importer import ParsedOutput
        parsed = [ParsedOutput(criteria="XEC ES-G34C5 0x00000001", mode="1920x1080", position="0 0")]
        outputs, unresolved = resolve_to_profile_outputs(parsed, [])
        self.assertEqual(outputs, [])
        self.assertEqual(unresolved, ["XEC ES-G34C5 0x00000001"])

    def test_missing_mode_falls_back_to_live_state(self):
        live = [make_monitor(name="DP-1")]
        from ezsway.core.importer import ParsedOutput
        parsed = [ParsedOutput(criteria="DP-1", position="0 0")]
        outputs, _ = resolve_to_profile_outputs(parsed, live)
        self.assertIn("1920x1080", outputs[0]["mode"])


if __name__ == "__main__":
    unittest.main()
