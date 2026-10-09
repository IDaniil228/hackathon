---
name: psql-branch-hands-off
description: "Ветку psql ведёт коллега; агенты её не пушат, не перематывают и не сливают в неё"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 735e8942-02bb-4c5b-a8e1-9ea36da1780b
  modified: 2026-10-09T23:52:17.675Z
---

Ветку `psql` (слой хранения) ведёт коллега. Агент её не трогает: не пушит в неё, не перематывает на `master` и не сливает в неё `master`. Слить `psql` в `master` можно, когда об этом просят

**Why:** пользователь запретил это прямо 2026-10-10, когда агент предложил перемотать `psql` на `master` после выноса Postgres в `db/docker-compose.yml`

**How to apply:** если раскладка в `master` разошлась с `psql`, скажи об этом пользователю, а не правь ветку. Связано с [[taskgen-test-db]]
