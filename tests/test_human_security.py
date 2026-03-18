import ast
import importlib
import os
import re

import pytest

PLUGIN_DIR = os.path.join(os.path.dirname(__file__), '..')
WORKFLOWS_DIR = os.path.join(PLUGIN_DIR, 'pyhuman', 'app', 'workflows')
DOWNLOAD_FILES_PATH = os.path.join(WORKFLOWS_DIR, 'download_files.py')
EXECUTE_COMMAND_PATH = os.path.join(WORKFLOWS_DIR, 'execute_command.py')
REQUIREMENTS_PATH = os.path.join(PLUGIN_DIR, 'requirements.txt')


class TestDownloadFilesSecurity:
    """Security tests for download_files.py."""

    @pytest.fixture(autouse=True)
    def _load_source(self):
        with open(DOWNLOAD_FILES_PATH, 'r') as f:
            self.source = f.read()
        self.tree = ast.parse(self.source)

    def test_no_verify_false(self):
        """Ensure requests calls use verify=True, never verify=False."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == 'verify':
                        assert not (
                            isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is False
                        ), (
                            f"Found verify=False at line {keyword.value.lineno} "
                            f"in download_files.py — this disables TLS certificate "
                            f"verification"
                        )

    def test_requests_calls_have_timeout(self):
        """Ensure every requests.get/post call includes a timeout parameter."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            # Detect requests.get(...), requests.post(...), etc.
            func = node.func
            is_requests_call = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == 'requests'
                and func.attr in ('get', 'post', 'put', 'patch', 'delete', 'head', 'options')
            )
            if is_requests_call:
                keyword_names = [kw.arg for kw in node.keywords]
                assert 'timeout' in keyword_names, (
                    f"requests.{func.attr}() call at line {node.lineno} "
                    f"in download_files.py is missing a timeout parameter"
                )

    def test_urllib_calls_have_timeout(self):
        """Ensure urllib.request.urlopen/urlretrieve calls include a timeout."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_urllib_call = (
                isinstance(func, ast.Attribute)
                and func.attr in ('urlopen', 'urlretrieve')
            )
            if is_urllib_call:
                keyword_names = [kw.arg for kw in node.keywords]
                assert 'timeout' in keyword_names, (
                    f"urllib call {func.attr}() at line {node.lineno} "
                    f"in download_files.py is missing a timeout parameter"
                )

    def test_no_ssl_import(self):
        """Ensure download_files.py does not import the ssl module."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != 'ssl', (
                        f"Found 'import ssl' at line {node.lineno} — "
                        f"ssl module should not be imported directly"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.module != 'ssl' and not (
                    node.module and node.module.startswith('ssl.')
                ), (
                    f"Found 'from ssl ...' import at line {node.lineno}"
                )

    def test_no_unverified_context(self):
        """Ensure _create_unverified_context is not referenced anywhere."""
        assert '_create_unverified_context' not in self.source, (
            "Found '_create_unverified_context' in download_files.py — "
            "this disables SSL certificate verification"
        )


class TestExecuteCommandSecurity:
    """Security tests for execute_command.py."""

    @pytest.fixture(autouse=True)
    def _load_source(self):
        with open(EXECUTE_COMMAND_PATH, 'r') as f:
            self.source = f.read()
        self.tree = ast.parse(self.source)

    def test_no_shell_true(self):
        """Ensure subprocess calls do not use shell=True."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_subprocess_call = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == 'subprocess'
            ) or (
                isinstance(func, ast.Name)
                and func.id in ('Popen', 'call', 'run', 'check_call', 'check_output')
            )
            if is_subprocess_call:
                for keyword in node.keywords:
                    if keyword.arg == 'shell':
                        assert not (
                            isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is True
                        ), (
                            f"Found shell=True at line {node.lineno} "
                            f"in execute_command.py — use shlex.split() with "
                            f"shell=False instead"
                        )


class TestRequirements:
    """Test that requirements.txt pins safe dependency versions."""

    def test_selenium_version_minimum(self):
        """Ensure selenium >= 4.14.0 to avoid known vulnerabilities."""
        with open(REQUIREMENTS_PATH, 'r') as f:
            content = f.read()

        # Find selenium line
        selenium_line = None
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('selenium'):
                selenium_line = stripped
                break

        assert selenium_line is not None, (
            "selenium not found in requirements.txt"
        )

        # Extract version
        match = re.search(r'selenium\s*[=<>!~]+\s*([0-9]+(?:\.[0-9]+)*)', selenium_line)
        assert match is not None, (
            f"Could not parse selenium version from: {selenium_line}"
        )
        version_str = match.group(1)
        version_parts = tuple(int(x) for x in version_str.split('.'))
        required_min = (4, 14, 0)
        assert version_parts >= required_min, (
            f"selenium version {version_str} is below minimum 4.14.0 — "
            f"upgrade to fix known CVEs"
        )


class TestWorkflowImports:
    """Ensure all workflow modules can be imported without error."""

    def _get_workflow_files(self):
        workflows = []
        for fname in os.listdir(WORKFLOWS_DIR):
            if fname.endswith('.py') and not fname.startswith('_'):
                workflows.append(fname)
        return sorted(workflows)

    def test_workflow_files_exist(self):
        """At least one workflow module should exist."""
        workflows = self._get_workflow_files()
        assert len(workflows) > 0, "No workflow files found"

    def test_workflow_files_parse(self):
        """All workflow .py files should be valid Python (parseable with ast)."""
        for fname in self._get_workflow_files():
            fpath = os.path.join(WORKFLOWS_DIR, fname)
            with open(fpath, 'r') as f:
                source = f.read()
            try:
                ast.parse(source)
            except SyntaxError as e:
                pytest.fail(
                    f"Workflow {fname} has a syntax error: {e}"
                )
