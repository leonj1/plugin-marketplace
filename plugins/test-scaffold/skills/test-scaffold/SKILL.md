---
description: Scaffold unit tests for selected code, detecting the project's test framework
disable-model-invocation: true
---

Generate unit tests for the selected code or file. Follow these steps:

1. **Detect the test framework** by checking project config files:
   - JavaScript/TypeScript: look for jest.config, vitest.config, .mocharc, or package.json test scripts
   - Python: look for pytest.ini, setup.cfg, pyproject.toml, or unittest usage
   - Go: use the standard `testing` package
   - Rust: use `#[cfg(test)]` module with standard assertions
   - Fall back to the most common framework for the language

2. **Generate test cases** covering:
   - Happy path with typical inputs
   - Edge cases (empty inputs, zero, null/undefined, boundary values)
   - Error cases (invalid inputs, expected exceptions)
   - Return value assertions

3. **Follow conventions**:
   - Use descriptive test names: `test_<function>_<scenario>_<expected>`
   - Group related tests in a describe/context block
   - Use the AAA pattern: Arrange, Act, Assert
   - Mock external dependencies; do not make network or filesystem calls
   - Match the project's existing test style if test files already exist

Output only the test file content. Place it in the conventional location for the detected framework.
