# Evidence: Blocked Merge Enforcement

Pull Request demonstration verifying branch protection gate enforcement:

- **PR #1:** `feat: test regression gating on golden set` (dev -> main)
- **Status:** Merging blocked
- **Failure:**
  - `Ephemeral Kind Cluster & Golden Evaluation Gate` failed:
    ```text
    GATE FAILED:
      - recall@5 (78.20%) dropped more than 5% below baseline (92.31%; floor is 87.31%)
    Error: Process completed with exit code 1.
    ```
- **GitHub Policy Output:**
  ```text
  Merging is blocked.
  Required status checks must pass before merging can be completed.
  1 failing and 4 successful checks.
  - Ephemeral Kind Cluster & Golden Evaluation Gate (Required) — Failing in 1m 42s
  ```
- **Conclusion:** Direct push and premature merge into `main` was successfully rejected by GitHub branch protection.
