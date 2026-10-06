# Agent instructions

This repository is a hackathon entry for the FSP special track: a platform that matches IT candidates and employers. The employer finds a verified candidate category and sends an invitation to the candidate

## Sources of truth

- The task statement is `docs/ФСП спец. трек ТЗ.pdf`. Its evaluation criteria set the priorities
- The team's product direction is `docs/Идеи.md`
- The database design is `docs/Структура БД.md`, and the diagram is `docs/ER.canvas`

Read the relevant document before a design decision. Do not copy its content into code comments or other documents: link to it

## Skills

The skills are git submodules in `.agents/skills/`, and `.claude/skills` is a symlink to that directory. Codex and opencode read `.agents/skills/`, and Claude Code reads `.claude/skills/`

- If a directory in `.agents/skills/` is empty, the clone did not fetch the submodules. Run `git submodule update --init` before other work
- Before an action that a skill's description names, load that skill
- Never edit a file inside `.agents/skills/`. A change goes to the skill's own repository, and this repository then moves the submodule to the new commit
- If `git status` shows a submodule as modified and nobody intended to move it, run `git submodule update --init`. Do not commit that change: it would move the submodule back to an old commit

## Commits

This repository uses the `ai-commit-trailers` skill as its AI-disclosure policy. Every commit that an agent writes or helps to write carries the trailer that the skill decides, with the value `<tool>:<model>` of the agent that made the change

## Language

Write code, code comments, commit messages and pull requests in English. The documents in `docs/` stay in Russian
