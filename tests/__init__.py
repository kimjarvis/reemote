# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Test-framework package for reemote.

Holds everything that exists exclusively to support testing the library --
most importantly ``simulated_host.SimulatedHost``, the in-memory stand-in
for a real machine. None of this ships in the ``reemote`` package under
``src/``; the library never imports from here. Examples borrow it (via a
``sys.path`` shim) only so they can run without a real SSH transport.
"""
