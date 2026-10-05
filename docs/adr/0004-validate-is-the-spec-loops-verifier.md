# Validate is the Spec loop's verifier, not a stage

There is no lifecycle controller. The `[validate]` gate set judges the integrated Spec; its findings become gap Issues that re-enter the next wave, bounded by `validate_cycles`. The "lifecycle" is one feedback edge.
