"""Counterexamples that must produce zero findings (guarded process handling)."""

import subprocess


def run_child(command):
    """Drain both pipes before inspecting the exit status."""
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = child.communicate()
    return child.returncode, stdout, stderr


def run_with_context(command):
    """The context manager reaps the child automatically."""
    with subprocess.Popen(command, stdout=subprocess.PIPE) as child:
        return child.communicate()[0]
