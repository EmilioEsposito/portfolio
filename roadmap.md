# Legend
* 🚧 indicate the current in progress epics/tasks
* ⏸️ indicate the paused epics/tasks
* ✅ indicate the completed epics/tasks
* [ ] indicate the epics/tasks that are not started yet

# DBOS

- ✅ DBOS hello world workflow
- ✅ De-noise DBOS sqlalchemy logging
- [ ] Replace APScheduler with DBOS scheduler: https://docs.dbos.dev/python/tutorials/scheduled-workflows
- [ ] Change email approval demo to not need its own table in the database
- [ ] Change email approval demo to do something else. Should be using email to do the approval. So the action it does should be different. 
- [ ] See if we should use DBOS events instead of relying on a manual resume that does another agent run. I think we need a way to just pause then resume a single agent run or something like that.