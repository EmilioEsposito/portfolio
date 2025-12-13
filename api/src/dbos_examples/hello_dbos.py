from dbos import DBOS



# Hello world DBOS (unrelated to this demo)
@DBOS.step()
def say_hello(name: str,iteration_number: int):
    return f"Hello, {name}! This is iteration {iteration_number}."

@DBOS.workflow()
def hello_workflow(iterations: int):
    for i in range(iterations):
        say_hello("World", i)

    return "Hello World Workflow completed"

