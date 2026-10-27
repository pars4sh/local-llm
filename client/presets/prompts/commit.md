Write a git commit message for the staged changes. If you have a terminal, run
`git diff --staged` in the repository I name; otherwise use the diff I paste.

Format:
- Subject: `<type>(<scope>): <subject>`
  - type: chore, docs, feat, fix, refactor, style or test
  - scope: cluster, gateway, client, evals, scripts, docs or repo
  - subject: imperative mood, at most 72 characters, no full stop
- A blank line, then a short body: what changed and why, wrapped at 72 columns.
- One logical change per commit. If the diff mixes several, say how to split it and write one
  message per part.
