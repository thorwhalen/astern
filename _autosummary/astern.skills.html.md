# astern.skills

The skills astern ships, and the command that makes an agent host see them.

The real files live in `astern/data/skills/<name>/SKILL.md` — inside the
package, so `pip install astern` carries them and `gh skill` (which matches
any non-hidden `**/skills/*/SKILL.md`) discovers them. Claude Code reads only
`.claude/skills/`, so this repo keeps a relative symlink there, and a *user*
gets the same bridge from [`install_skills()`](#astern.skills.install_skills).

Assets are **discovered from the directory**, never listed here: shipping a new
skill is adding a directory. Installing one is a symlink, so upgrading the
package upgrades the skill; where symlinks are unavailable, a copy, and the plan
says which happened. Nothing already at a destination is overwritten — a foreign
file of the same name reads `conflict` and stays as it was until `force`.

```pycon
>>> [asset.name for asset in bundled()]
['astern-recall']
```

### Functions

| [`bundled`](#astern.skills.bundled)()                                          | Every skill this package ships, discovered from `astern/data/skills`.     |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`claude_home`](#astern.skills.claude_home)([target])                              | Where an agent host looks: `target` → `$CLAUDE_CONFIG_DIR` → `~/.claude`. |
| [`install_skills`](#astern.skills.install_skills)(\*[, target, only, force, dry_run]) | Make the bundled skills visible to Claude Code.                           |

### Classes

| [`Asset`](#astern.skills.Asset)(kind, name, source)   | One installable thing: a skill directory shipped inside the package.   |
|------------------------------------------------------------------------------|------------------------------------------------------------------------|

### *class* astern.skills.Asset(kind, name, source)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One installable thing: a skill directory shipped inside the package.

### astern.skills.bundled()

Every skill this package ships, discovered from `astern/data/skills`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Asset`](#astern.skills.Asset)]

### astern.skills.claude_home(target=None)

Where an agent host looks: `target` → `$CLAUDE_CONFIG_DIR` → `~/.claude`.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### astern.skills.install_skills(, target=None, only=None, force=False, dry_run=False)

Make the bundled skills visible to Claude Code. Idempotent.

Links each into `target` (default `$CLAUDE_CONFIG_DIR` or `~/.claude`).
Returns the plan: one row per asset with its action — `install`, `ok`
(already ours) or `conflict` (something else is there; left alone unless
`force`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> plan = install_skills(target='/nonexistent/host', dry_run=True)
>>> plan['counts']
{'install': 1, 'ok': 0, 'conflict': 0}
```
