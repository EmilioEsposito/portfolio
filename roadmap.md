# Legend
* 🚧 indicate the current in progress epics/tasks
* ⏸️ indicate the paused epics/tasks
* ✅ indicate the completed epics/tasks
* [ ] indicate the epics/tasks that are not started yet

# DBOS

- ✅ DBOS hello world workflow
- ✅ De-noise DBOS sqlalchemy logging
- ✅ Replace APScheduler jobs with DBOS scheduler for static jobs: https://docs.dbos.dev/python/tutorials/scheduled-workflows (also see hello_dbos.py for an example). Scheduler routes retained for frontend compatibility.
- 🚧 HITL Agents. 
    - 🚧 handle case with multiple pending approvals
    - [ ] make more robust user-level security at the API *and* DB level. If user A's conversation_id is leaked, User B (even if authenticated) should not be able to access it. Right now they could. 