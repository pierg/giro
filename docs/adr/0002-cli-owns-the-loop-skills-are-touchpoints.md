# The CLI is the product; skills are the touchpoints

Rejected the skills-outside architecture (an LLM interpreting loop control each turn — an earlier experiment that produced five controllers and 5.5k LOC of scripts). The `giro` CLI runs headless-parity loops; skills remain first-class readable SKILL.md files at exactly the LLM touchpoints: the chat doorway and the spawned contexts.
