# Zillow Leads Agent changes

Read [README.md](README.md) and [QUEUE.md](QUEUE.md) before changing the runtime.
Runtime prompts in `prompts/`, tool/output descriptions, and Pydantic field
descriptions affect behavior; do not edit them as documentation-only cleanup.

Keep this package independent of `sernia_ai`. Shared services belong in their
owning integration or conversation module. The general agent's adapter for this
package's leasing policy lives in `sernia_ai/lead_policy.py`.

Keep applicant emails, eval scenarios, labels, and generated review pages in
ignored `evals/data/`. Never force-add them. Tests belong in `api/src/tests/`.
