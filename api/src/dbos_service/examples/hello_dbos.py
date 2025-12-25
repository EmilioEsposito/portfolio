from dbos import DBOS
import datetime
import logfire



# 1. Hello world DBOS (unrelated to this demo)
@DBOS.step()
def say_hello(name: str,iteration_number: int):
    return f"Hello, {name}! This is iteration {iteration_number}."

@DBOS.workflow()
def hello_workflow(iterations: int):
    for i in range(iterations):
        say_hello("World", i)

    return "Hello World Workflow completed"

# 2. Scheduled workflow
# https://docs.dbos.dev/python/tutorials/scheduled-workflows
@DBOS.scheduled("3 19 * * *") 
@DBOS.workflow()
async def scheduled_workflow_example(scheduled_time: datetime.datetime, actual_time: datetime.datetime):
    logfire.info(f"scheduled_workflow_example: Scheduled: {scheduled_time}, actual: {actual_time}")
