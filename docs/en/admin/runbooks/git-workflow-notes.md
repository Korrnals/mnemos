# Runbook: Git Workflow Notes

**🌐 Language / Язык:** English · [Русский](../../../ru/admin/runbooks/git-workflow-notes.md)

## Squash-merge and `git branch -d`

After a squash-merge PR, `git branch -d <feature-branch>` may print:

```
warning: deleting branch that has been merged to 'refs/remotes/origin/...'
but not yet merged to HEAD
```

This is **expected**. The squash commit on `main` is not the same as the
original branch commits, so git's merge detection does not recognize it. The
branch IS safely merged (the remote ref was merged before squash). `git branch
-d` is safe despite the warning; use `git branch -D` only if you are certain
the branch is no longer needed and was not merged.