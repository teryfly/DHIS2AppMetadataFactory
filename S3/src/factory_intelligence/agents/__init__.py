"""S3 agents package.

Deliberately a docstring only.  The public surface of this subsystem is
factory_intelligence.api (00 doc 6.1, D-13), and re-exporting implementation
modules from here made every intra-package import depend on package initialisation
order, which produced a genuine circular-import failure: importing one leaf module
executed this file, which imported a sibling that imported back.  S0 does the same
thing for db, common and progress.
"""

from __future__ import annotations
