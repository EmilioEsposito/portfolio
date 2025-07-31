from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv('.env.development.local'))
import os
import requests
from pprint import pprint
import pytest
from datetime import datetime
import pytz
from api.src.open_phone.service import send_message
import logging
from api.src.scheduler.service import scheduler
from apscheduler.triggers.cron import CronTrigger
from api.src.contact.service import get_contact_by_slug
import json

logger = logging.getLogger(__name__)


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

    logger.info(f"Found {len(tasks)} tasks")
    logger.info(f"Today's date: {today_et.date()}")
    logger.info(f"Today's weekday: {today_et.weekday()}")
    

    # filter for tasks (due today or overdue) AND not completed
    for task in tasks:
        task_due_date = datetime.fromtimestamp(int(task['due_date']) / 1000)
        task['due_date_pretty'] = task_due_date.strftime("%Y-%m-%d")
        logger.debug(f"Task: {task['name']}, Due: {task['due_date_pretty']}, Status: {task['status']['status']}, List ID: {task['list']['id']}")
        # filter for tasks due today
        if task_due_date.date() <= today_et.date():
            # filter out completed tasks
            if task['status']['status'] != 'complete':
                pretty_task_str = json.dumps(task, indent=4)
                logger.info(f"task payload: {pretty_task_str}")

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

    if len(tasks_filtered) > 0:

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
        
        logger.info(filtered_tasks_str)

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
        logger.info(f"Sent Task Reminder message to {to_phone_number}")
    else:
        logger.info("No tasks due today")

    return filtered_tasks_str



async def start_service():
    # test job
    scheduler.add_job(
        id="clickup_peppino_tasks",
        func=get_peppino_view_tasks,
        trigger=CronTrigger(hour="8,17", minute="0", timezone="US/Eastern"),
        coalesce=True,
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300
    )


@pytest.mark.asyncio
async def test_get_peppino_view_tasks():
    await get_peppino_view_tasks()