# Local-only project

This repository is intended to stay **on this machine only**.

- **Path:** `C:\Users\Owner\Desktop\Solana`
- **Git:** Local history only (no `git remote` configured).
- **Do not** add a remote or run `git push` unless you explicitly choose to publish elsewhere.

To verify:

```powershell
cd C:\Users\Owner\Desktop\Solana
& "C:\Program Files\Git\cmd\git.exe" remote -v
```

(No output means no remotes.)

Secrets and environments are ignored via `.gitignore` (`.env`, `.venv/`, `venv/`, etc.).
