---
description: Review code for bugs, security vulnerabilities, performance issues, and style violations
disable-model-invocation: true
---

Review the selected code or recent changes. Focus on these areas in order of priority:

1. **Security vulnerabilities**: injection flaws, exposed secrets, unsafe deserialization, improper auth checks
2. **Bugs and correctness**: null/undefined access, off-by-one errors, race conditions, unhandled errors
3. **Performance**: unnecessary allocations, N+1 queries, missing indexes, blocking operations in async code
4. **Readability and maintainability**: unclear naming, excessive complexity, missing error context

For each issue found:
- State the severity: CRITICAL, WARNING, or INFO
- Show the exact line or code block
- Explain why it is a problem
- Suggest a concrete fix

Be concise and actionable. Skip praise and filler. If the code looks good, say so in one sentence.
