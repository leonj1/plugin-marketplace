---
description: Generate a conventional commit message from staged changes
disable-model-invocation: true
---

Analyze the currently staged changes (`git diff --cached`) and generate a commit message following the Conventional Commits specification:

Format: `<type>(<scope>): <description>`

Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert

Rules:
- The description must be lowercase, imperative mood, no period at the end
- Keep the first line under 72 characters
- Add a body only if the change is non-obvious; separate with a blank line
- Add `BREAKING CHANGE:` footer for breaking changes
- Scope is optional but recommended; derive it from the primary file/module changed

Output only the commit message, nothing else. If multiple logical changes are staged, suggest splitting them and provide a message for each.
