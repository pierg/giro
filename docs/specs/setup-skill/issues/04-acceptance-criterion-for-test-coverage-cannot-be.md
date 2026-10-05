---
state: done
blocked_by: []
attempts: 1
---
# Acceptance criterion for test coverage cannot be demonstrated until the 

The Issue #02 acceptance criteria require that `tests/test_setup_docs.py` gains a new test locking `cmd_init`'s 'Next:' copy in `src/giro/cli.py`. The test was added but will crash before asserting anything, so the coverage requirement is not actually met. The test needs to be rewritten to handle the multi-line string format in the cli.py code.

Filed by validate gate `spec-fit` in gap cycle 1.

## Attempt 1 — done — all gates green

- worker: Rewrote test_cmd_init_next_copy_points_at_install_and_setup (and its helper) in tests/test_setup_docs.py to extract cmd_init's 'Next:' print message via ast.parse instead of a raw text.index('")', ...) offset search. The old approach crashed with ValueError whenever the closing quote and paren of the print(...) call landed on separate lines (the exact multi-line format bug this Issue describes). The AST approach reads the literal string argument directly, so it's immune to how the source wraps or joins the adjacent string literals — verified it correctly extracts the message from both the current cli.py and the pre-Issue-03 multi-line version. Only tests/test_setup_docs.py changed; full suite passes (95 passed).
- notes: Issue #03 (already done) fixed the same underlying gap-cycle-1 finding by reformatting cli.py so the quote and paren were adjacent, which made the brittle test pass by coincidence rather than by being correct. This attempt makes the test itself robust instead, so it no longer depends on cli.py's specific line-wrapping.
