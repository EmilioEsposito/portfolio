from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv('.env'))
import os
import requests
from pprint import pprint
import pytest
from datetime import datetime
import pytz
from api.src.open_phone.service import send_message
import logfire
from api.src.contact.service import get_contact_by_slug
import json
# DBOS DISABLED: $75/month DB keep-alive costs too high for hobby project.
# See api/src/schedulers/README.md for re-enabling instructions.
# from dbos import DBOS



CLICKUP_API_KEY = os.getenv("CLICKUP_API_KEY")

if not CLICKUP_API_KEY:
    raise ValueError("CLICKUP_API_KEY must be set")


headers = {
    "accept": "application/json",
    "Authorization": CLICKUP_API_KEY
}


async def get_peppino_view_tasks():
    """
    Get all tasks from a view.
    """
    peppino_view_id = "2ky3xg85-573"
    url = f"https://api.clickup.com/api/v2/view/{peppino_view_id}/task"

    response = requests.get(url, headers=headers)
    tasks = response.json()['tasks']
    #filter for tasks due today OR overdue
    today_et = datetime.now(pytz.timezone('US/Eastern'))
    tasks_filtered = []

    logfire.info(f"Found {len(tasks)} tasks")
    logfire.info(f"Today's date: {today_et.date()}")
    logfire.info(f"Today's weekday: {today_et.weekday()}")
    

    # filter for tasks (due today or overdue) AND not completed
    for task in tasks:
        due_date = task.get('due_date') or 0 # in case due_date is None
        task_due_date = datetime.fromtimestamp(int(due_date) / 1000)
        task['due_date_pretty'] = task_due_date.strftime("%Y-%m-%d")
        logfire.debug(f"Task: {task['name']}, Due: {task['due_date_pretty']}, Status: {task['status']['status']}, List ID: {task['list']['id']}")
        # filter for tasks due today
        if task_due_date.date() <= today_et.date():
            # filter out completed tasks
            if task['status']['status'] != 'complete':
                pretty_task_str = json.dumps(task, indent=4)
                logfire.info(f"task payload: {pretty_task_str}")

                # for low overdue priority tasks, only append if it's Friday
                if task.get('priority') and task.get('priority', {}).get('priority','') == 'low':
                    if today_et.weekday() == 4:
                        tasks_filtered.append(task)
                    continue
                # otherwise, remind every time
                else:
                    tasks_filtered.append(task)



    # sort tasks by due date
    tasks_filtered.sort(key=lambda x: x['due_date_pretty'])

    filtered_tasks_str = "Sernia Task Reminder - Reply with updates"

    if len(tasks_filtered) > 0 or os.getenv("RAILWAY_ENVIRONMENT_NAME","local")!='production':

        for task in tasks_filtered:
            is_maintenance_task = task['list']['id'] == "901312027371"
            # format the tasks_filtered nicely for an AI to read
            task_template = f"\n-------------"
            task_template += f"\nTask: {task['name']}"
            if is_maintenance_task:
                task_template += f"(Maintenance Request)"
            task_template += f"\nDue: {task['due_date_pretty']}"

            if is_maintenance_task:
                task_template += f"\nSee details: {task['url']}"
            
            filtered_tasks_str += task_template
        
        logfire.info(filtered_tasks_str)

        # send message to sernia
        env = os.getenv("RAILWAY_ENVIRONMENT_NAME","local")
        if env=="production":
            target_contact = await get_contact_by_slug("peppino")
        else:
            target_contact = await get_contact_by_slug("emilio")
            filtered_tasks_str = f"ENV: {env}\n{filtered_tasks_str}"
        to_phone_number = target_contact.phone_number

        await send_message(
            message=filtered_tasks_str,
            to_phone_number=to_phone_number,
            from_phone_number="+14129101500"
        )
        logfire.info(f"Sent Task Reminder message to {to_phone_number}")
    else:
        logfire.info("No tasks due today")

    return filtered_tasks_str



# --- APScheduler Job Registration ---

def register_clickup_apscheduler_jobs():
    """Register ClickUp scheduled jobs with APScheduler.

    Runs at 8am and 5pm ET (13:00 and 21:00 UTC).
    """
    from api.src.apscheduler_service.service import get_scheduler

    scheduler = get_scheduler()
    scheduler.add_job(
        func=get_peppino_view_tasks,
        trigger="cron",
        hour="13,21",  # 8am and 5pm ET = 13:00 and 21:00 UTC
        minute=0,
        id="clickup_peppino_tasks_scheduled",
        replace_existing=True,
        name="ClickUp Peppino Tasks Reminder",
    )
    logfire.info("ClickUp APScheduler jobs registered.")


# DBOS DISABLED: Moved to APScheduler. See api/src/schedulers/README.md for re-enabling.
# # DBOS Scheduled Workflow: Run at 8am and 5pm ET
# # Cron: minute hour day month weekday
# # "0 8,17 * * *" = at minute 0 of hours 8 and 17, every day
# @DBOS.scheduled("0 13,21 * * *")
# @DBOS.workflow()
# async def clickup_peppino_tasks_scheduled(scheduled_time: datetime, actual_time: datetime):
#     """DBOS scheduled workflow for ClickUp Peppino tasks reminder."""
#     logfire.info(f"clickup_peppino_tasks_scheduled: Scheduled: {scheduled_time}, actual: {actual_time}")
#     await get_peppino_view_tasks()
#
# def register_clickup_dbos_jobs():
#     logfire.info("ClickUp DBOS jobs registered.")

@pytest.mark.asyncio
async def test_get_peppino_view_tasks():
    await get_peppino_view_tasks()
