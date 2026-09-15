"""Every declared Python version must agree, and satisfy Django. Audit F08.

Three files each declared a version: Dockerfile 3.11, render.yaml 3.11.0,
runtime.txt 3.12.0. Django 6 declares Requires-Python >=3.12, so the two 3.11
paths cannot install requirements.txt at all - confirmed by resolving Django 6
for a 3.11 target (no matching distribution) and a 3.12 target (resolves),
same machine and network, so only the version differed.

On Render, PYTHON_VERSION outranks runtime.txt, which is what made the stale
render.yaml value dangerous rather than merely untidy.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
DJANGO_MINIMUM = (3, 12)


def _major_minor(version):
    major, minor = version.split('.')[:2]
    return int(major), int(minor)


def declared_versions():
    versions = {}

    dockerfile = (BASE / 'Dockerfile').read_text(encoding='utf-8')
    match = re.search(r'^FROM\s+python:(\d+\.\d+)', dockerfile, re.M)
    versions['Dockerfile'] = match.group(1) if match else None

    render = (BASE / 'render.yaml').read_text(encoding='utf-8')
    match = re.search(r'key:\s*PYTHON_VERSION\s*\n\s*value:\s*"?(\d+\.\d+(?:\.\d+)?)"?',
                      render)
    versions['render.yaml'] = match.group(1) if match else None

    runtime = (BASE / 'runtime.txt').read_text(encoding='utf-8').strip()
    match = re.match(r'python-(\d+\.\d+(?:\.\d+)?)', runtime)
    versions['runtime.txt'] = match.group(1) if match else None

    return versions


class RuntimeAlignmentTests(SimpleTestCase):

    def test_every_deployment_path_declares_a_version(self):
        missing = [name for name, v in declared_versions().items() if v is None]
        self.assertEqual(missing, [], 'could not read a Python version from these')

    def test_every_declared_version_satisfies_django(self):
        too_old = {name: v for name, v in declared_versions().items()
                   if v and _major_minor(v) < DJANGO_MINIMUM}
        self.assertEqual(too_old, {},
                         'these cannot install Django 6, which requires Python 3.12+')

    def test_every_declared_version_is_the_same_minor_release(self):
        """Patch releases may differ (the Docker tag has none); the minor
        must not, or two deployment paths run different interpreters."""
        minors = {name: _major_minor(v) for name, v in declared_versions().items() if v}
        self.assertEqual(len(set(minors.values())), 1, f'declarations disagree: {minors}')

    def test_the_minimum_matches_what_django_itself_requires(self):
        """If Django is upgraded past what this constant says, the test above
        should be judging against the new floor, not a stale one."""
        from importlib.metadata import metadata
        requires = metadata('Django').get('Requires-Python', '')
        match = re.search(r'>=\s*(\d+)\.(\d+)', requires)
        self.assertIsNotNone(match, requires)
        self.assertLessEqual((int(match.group(1)), int(match.group(2))), DJANGO_MINIMUM)
