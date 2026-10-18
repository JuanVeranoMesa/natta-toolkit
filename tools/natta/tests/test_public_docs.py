"""Public documentation states current, verifiable facts only."""
from pathlib import Path
import re
import unittest

import natta

REPOSITORY = natta.ROOT.parent.parent
PUBLIC_DOCS = (REPOSITORY / 'README.md', REPOSITORY / 'AGENTS.md', *sorted((REPOSITORY / 'docs').glob('*.md')),
               natta.ROOT / 'README.md', *sorted((natta.ROOT / 'docs').glob('*.md')))


class PublicDocumentationTests(unittest.TestCase):
    def text(self, path):
        return ' '.join(path.read_text().split())

    def test_no_stale_quality_or_personal_status_claims(self):
        for path in PUBLIC_DOCS:
            with self.subTest(path=path.relative_to(REPOSITORY)):
                text = self.text(path).lower()
                for phrase in ('no confirmed remaining', 'verified personal implementation',
                               'no bugs remain', '441 passing tests', 'verified source architecture'):
                    self.assertNotIn(phrase, text)

    def test_no_references_to_unpublished_review_reports(self):
        for path in PUBLIC_DOCS:
            with self.subTest(path=path.relative_to(REPOSITORY)):
                self.assertIsNone(re.search(r'review report', self.text(path), re.I))

    def test_readme_test_count_matches_suite(self):
        count = unittest.defaultTestLoader.discover(str(natta.ROOT / 'tests'), top_level_dir=str(natta.ROOT / 'tests')).countTestCases()
        counts = re.findall(r'\((\d+) tests at this revision\)', self.text(REPOSITORY / 'README.md'))
        self.assertEqual(counts, [str(count)])

    def test_git_filter_boundary_is_documented_without_blanket_lfs_incompatibility(self):
        for path in (REPOSITORY / 'README.md', REPOSITORY / 'docs/SECURITY.md', natta.ROOT / 'docs/RUNTIME_EFFECTS.md'):
            with self.subTest(path=path.relative_to(REPOSITORY)):
                text = self.text(path)
                self.assertIn('external_filter_configured', text)
                self.assertIn('Git LFS', text)
                self.assertIn('filter=lfs', text)
                self.assertIn('submodule_unsupported', text)
                self.assertRegex(text, r'does not (currently )?support repositories containing Git submodules')
                self.assertNotIn('while any clean/process filter is configured', text)
                self.assertNotIn('While any driver is configured', text)


class PublicTextResidueTests(unittest.TestCase):
    """Confirmed private/internal residue stays out of user-facing text (NTT-008)."""

    def test_help_uses_public_concepts(self):
        import contextlib, io
        for command in (None, *natta.capabilities.COMMANDS):
            out = io.StringIO()
            with self.subTest(command=command), contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                natta.make_parser().parse_args([command, '--help'] if command else ['--help'])
            text = out.getvalue()
            self.assertIsNone(re.search(r'\bLuna\b|\bPhase \d', text), text)
        self.assertIn('semantic router', natta.capabilities.COMMANDS['route'].summary)

    def test_generic_xcode_verification_has_no_app_specific_hardware(self):
        source = (natta.ROOT / 'adapters.py').read_text()
        self.assertNotIn('Geofence', source)
        self.assertIn('Hardware-dependent, distribution and hands-on upgrade checks', source)

    def test_product_docs_use_public_model_terms(self):
        for path in PUBLIC_DOCS:
            with self.subTest(path=path.relative_to(REPOSITORY)):
                self.assertIsNone(re.search(r'\bLuna\b', path.read_text()))

    def test_generated_research_report_links_only_shipped_files(self):
        source = (REPOSITORY / 'tools/natta-local-model/compare_selection.py').read_text()
        for residue in ('PHASE4C.md', 'Ghostty'):
            self.assertNotIn(residue, source)
        self.assertIn('(../README.md)', source)
        self.assertTrue((REPOSITORY / 'tools/natta-local-model/README.md').is_file())


if __name__ == '__main__':
    unittest.main()
