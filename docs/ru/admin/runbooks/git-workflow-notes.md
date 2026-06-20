# Runbook: Заметки по Git-workflow

**🌐 Language / Язык:** [English](../../../en/admin/runbooks/git-workflow-notes.md) · Русский

## Squash-merge и `git branch -d`

После squash-merge PR команда `git branch -d <feature-branch>` может выдать:

```
warning: deleting branch that has been merged to 'refs/remotes/origin/...'
but not yet merged to HEAD
```

Это **ожидаемое поведение**. Squash-коммит в `main` не совпадает побайтово с
исходными коммитами ветки, поэтому определение слияния git его не распознаёт.
Ветка **безопасно слита** — remote-ref был смержён до squash. `git branch -d`
безопасна несмотря на предупреждение; `git branch -D` нужна только если вы
уверены, что ветка больше не нужна и не была слита.