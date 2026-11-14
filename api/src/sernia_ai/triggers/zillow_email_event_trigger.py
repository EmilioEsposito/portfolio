"""Compatibility for APScheduler jobs persisted before the Zillow package move.

New registrations use ``api.src.zillow_leads.queue``. Keep this import path so
SQLAlchemyJobStore can deserialize an existing job before startup upserts it.
"""

from api.src.zillow_leads.queue import process_zillow_email_queue as process_zillow_email_queue
