---
description: Analyze code for refactoring opportunities and apply improvements
disable-model-invocation: true
---

Analyze the selected code or file for refactoring opportunities. Look for:

1. **Code duplication**: repeated logic that should be extracted into shared functions
2. **Long functions**: functions doing too much that should be split by responsibility
3. **Deep nesting**: excessive if/else or loop nesting that can be flattened with early returns or extraction
4. **God objects**: classes with too many responsibilities that should be decomposed
5. **Magic values**: hardcoded strings and numbers that should be named constants
6. **Dead code**: unused variables, unreachable branches, commented-out code
7. **Poor abstractions**: leaky abstractions, unnecessary indirection, or missing abstractions
8. **Type safety**: places where stronger typing would prevent bugs

For each opportunity found:
- Describe the problem in one sentence
- Show the current code
- Show the refactored code
- Explain the benefit (readability, testability, performance, safety)

Apply refactorings that are safe and improve clarity. Preserve external behavior. Do not change public APIs unless explicitly asked.
