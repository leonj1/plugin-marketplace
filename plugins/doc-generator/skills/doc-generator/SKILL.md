---
description: Generate documentation for code including functions, classes, and modules
disable-model-invocation: true
---

Generate documentation for the selected code or file. Detect the language and use the appropriate documentation format:

- **JavaScript/TypeScript**: JSDoc with `@param`, `@returns`, `@throws`, `@example`
- **Python**: Google-style docstrings with Args, Returns, Raises, Example sections
- **Go**: Godoc-style comments starting with the function/type name
- **Rust**: `///` doc comments with `# Arguments`, `# Returns`, `# Errors`, `# Examples`
- **Java/Kotlin**: Javadoc with `@param`, `@return`, `@throws`
- **Other languages**: Use the most common documentation convention

Rules:
- Document the purpose, not the implementation
- Include parameter types and descriptions
- Document return values and possible errors/exceptions
- Add a usage example for non-trivial functions
- For classes/modules, add a top-level description of responsibility
- Keep descriptions concise; one sentence per parameter is enough
- Do not document obvious getters/setters unless they have side effects

Output the documented code, preserving the original implementation.
