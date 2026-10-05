# One build verb, routed by the target

`giro implement <target>` is the only build entrypoint: an Issue runs the Issue loop, a Spec runs the Spec loop, an empty Spec is planned first. No mode flags, no second entrypoint — two entrypoints with different semantics was the surface bug that sank the predecessor.
