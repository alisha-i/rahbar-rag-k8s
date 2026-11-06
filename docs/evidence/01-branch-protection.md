# Evidence: Branch Protection Configuration

Repository branch protection rule enforced on `main`:
- **Branch pattern:** `main`
- **Require a pull request before merging:** Enabled
  - Require approvals: 1
  - Dismiss stale pull request approvals when new commits are pushed: Enabled
- **Require status checks to pass before merging:** Enabled
  - Status checks required:
    - `Lint & Code Style` (`ruff`)
    - `Unit & Pipeline Tests` (`pytest`)
    - `Kubeconform Manifest Validation` (`kubeconform`)
    - `Docker Build & Vulnerability Scan` (`trivy`)
    - `Ephemeral Kind Cluster & Golden Evaluation Gate` (`eval/run_eval.py`)
- **Require branches to be up to date before merging:** Enabled
- **Require linear history:** Enabled
- **Do not allow bypassing the above settings:** Enabled
