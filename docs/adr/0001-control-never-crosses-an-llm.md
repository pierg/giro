# Control never crosses an LLM

The engine — deterministic Python — owns every state transition, budget, gate execution, wave schedule, and commit. LLM contexts do the creative work inside boundaries the engine draws, and return JSON envelopes. A budget enforced by a `while` loop cannot be talked out of; an LLM "following instructions" can. This is giro's founding decision; everything else derives from it.
