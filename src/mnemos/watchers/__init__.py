"""File watchers for Mnemos.

Submodules:
  vault       — General vault watcher (debounce + batching → pipeline triggers)
  path_scoped — M8: watches .github/instructions/*.instructions.md
                Parses frontmatter (applyTo: glob) + body, creates Memory
                with status=published, tags gcw:rule + project: + applyTo: +
                source:path-scoped-rule.
                On file change → update. On delete → remove memory + vector entry.
"""
