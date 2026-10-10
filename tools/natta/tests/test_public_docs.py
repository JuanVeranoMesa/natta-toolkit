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


if __name__ == '__main__':
    unittest.main()
