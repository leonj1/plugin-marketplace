---
description: Suggest a branch name following team conventions from a task description
disable-model-invocation: true
---

Given a task description or ticket reference, generate a branch name following this convention:

Format: `<type>/<short-description>`

Types:
- `feature/` for new features
- `fix/` for bug fixes
- `hotfix/` for urgent production fixes
- `refactor/` for code refactoring
- `docs/` for documentation changes
- `test/` for adding or updating tests
- `chore/` for maintenance tasks

Rules:
- Use lowercase kebab-case for the description
- Keep it under 50 characters total
- Include ticket number if provided (e.g., `feature/PROJ-123-add-user-auth`)
- Be descriptive but concise

Output only the branch name, nothing else.
