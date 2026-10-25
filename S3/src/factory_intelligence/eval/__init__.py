"""S3 evaluation: packaged sets, scoring and the runner (AI-EVAL-001 … AI-EVAL-003).

Docstring only, as in ``agents``, ``output``, ``llm`` and ``context``: this subsystem's entry
point is :func:`factory_intelligence.eval.runner.run_evaluation`, reached by module path rather
than re-exported here, because re-exporting implementation modules from a package root made
every intra-package import depend on package initialisation order (see ``agents/__init__.py``).
The case file format that ``run_evaluation`` reads is documented in
:mod:`factory_intelligence.eval.scoring`.

Nothing in this package touches a network.  The provider is whatever
:func:`factory_intelligence.deps.get_llm_provider` returns, which is how the mock agent and the
record-replay adapter (AI-MOCK-001, AI-LLM-006) let a whole evaluation run offline, and why an
evaluation set can be shipped in the wheel and re-run by a deployment (AI-EVAL-002).
"""

from __future__ import annotations
