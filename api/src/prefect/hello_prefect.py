from prefect import flow, task
import random
from api.src.google.gmail.service import send_email
from api.src.google.common.service_account_auth import get_delegated_credentials
import asyncio

@task
def get_customer_ids() -> list[str]:
    # Fetch customer IDs from a database or API
    return [f"customer{n}" for n in random.choices(range(100), k=10)]

@task
def process_customer(customer_id: str) -> str:
    # Process a single customer
    return f"Processed {customer_id}"

@task
async def send_email_task(body: str) -> None:
    await send_email(
        to="espo412@gmail.com",
        subject="Hello from Prefect", 
        message_text=body, 
        credentials=get_delegated_credentials(
        user_email="emilio@serniacapital.com",
        scopes=["https://mail.google.com"],
    )
    )
    
@flow
async def main() -> list[str]:
    customer_ids = get_customer_ids()
    # Map the process_customer task across all customer IDs
    results = process_customer.map(customer_ids)
    body = f"This is a test email from Prefect. Customer IDs: {str(customer_ids)}"
    await send_email_task(body=body)
    return results


if __name__ == "__main__":
    # run it locally
    # asyncio.run(main())

    # DEPLOYMENTS
    # 1. Host a worker process that runs the flow
    # Create a scheduled deployment that runs daily at 2 AM UTC
    # This runs long running worker process
    main.serve(
        name="hello-prefect-deployment",
        cron="0 0 * * *"  # Runs daily at 12:00 AM UTC
    )

    # # 2. TODO: Fix this. Use a Prefect cloud managed pool. Not Working yet.
    # # https://docs-3.prefect.io/v3/how-to-guides/deployments/create-deployments
    # main.deploy(
    #     name="hello-remote-deployment",
    #     cron="0 0 * * *",
    #     work_pool_name="emilio-managed-pool",
    #     image="prefecthq/prefect-client:3-python3.11",
    # )
