# Legend
* 🚧 indicate the current in progress epics/tasks
* ⏸️ indicate the paused epics/tasks
* ✅ indicate the completed epics/tasks
* [ ] indicate the epics/tasks that are not started yet

# DBOS

- ✅ DBOS hello world workflow
- ✅ De-noise DBOS sqlalchemy logging
- ✅ Replace APScheduler jobs with DBOS scheduler for static jobs: https://docs.dbos.dev/python/tutorials/scheduled-workflows (also see hello_dbos.py for an example). Scheduler routes retained for frontend compatibility.
- ✅ Let's retain example of APScheduler for future use. We realized that APScheduler might actually still be useful in the future since it can do tenant level dynamic cron jobs. We can give those as tools to an AI agent later to dynamically schedule (maybe not yet though). Let's figure out a way for DBOS and APScheduler examples to coexist without too much confusion. Maybe let's centralize dbos under its own folder dbos_service, and apscheduler under apscheduler_service, then a combined schedulers folder with unified route that is thin wrapper around the underlying scheduler endpoints. 
- ✅ Document the dual-scheduler approach (DBOS + APScheduler) and the unified `/schedulers` API in `api/src/schedulers/README.md`.
- [ ] Change email approval demo to not need its own table in the database. I think better to wrap the email approval demo in a DBOS workflow and have it wait for an event. See human_in_the_loop_agent.py for an example of I think how this should be done (note that code hasn't been tested, but think conceptually it is better pattern)
- [ ] Change email approval demo to do instead send a test sms message to Emilio (the AI should be able to customize the message). The invocation of the send_sms tool should require approval, and the approval should be able to happen either via the web UI or via an email sent to Emilio that has a review button that directs to the Web UI. The AI drafted SMS should be visible on the review page with buttons for Approve/Deny/. Should be using email to *do* the approval (I have a function to send_email). So the action needing approval it does should be just dummy for now.  
