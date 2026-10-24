"""Intentional defects for AIT-Z01 / AIT-Z02 / AIT-Z03 (never imported by tests)."""

import multiprocessing
import subprocess


def run_chatty_child(command):
    """AIT-Z01: pipe-backed Popen is waited on without draining the pipe."""
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child.wait()
    return child.returncode


def launch_without_reaping(command):
    """AIT-Z02: the handle is dropped and the child is never reaped."""
    child = subprocess.Popen(command)
    return child.pid


def work():
    """Trivial worker used by the import-time spawn below."""
    return None


process = multiprocessing.get_context("spawn").Process(target=work)
process.start()
process.join()
