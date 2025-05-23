# Backend Task

Create a custom FastAPI route for the the following SQL query.

```sql
SELECT 
    EM.MESSAGE_ID,
    EM.THREAD_ID,
    EM.RECEIVED_DATE,
    EM.SUBJECT,
    EM.BODY_HTML -- HTML IS SHOWN IN SOME SORT OF CONTAINER WHEN ROW IS CLICKED
FROM EMAIL_MESSAGES AS EM
WHERE EM.BODY_HTML ILIKE '%ZILLOW%' AND SUBJECT NOT LIKE '%DAILY LISTING%'
ORDER BY RANDOM()
LIMIT 5 
;
```

Use route name `/api/google/gmail/get_zillow_emails`.



# Frontend Task 

I want to create a new frontend feature that allows one to test out an AI-powered responses to Zillow rental email messages. 

Core Elements:

1. "Select a Sample Email"
A scrollable table of 5 random Zillow emails from the database. Fetch the data using a GET request to the FastAPI route `/api/google/gmail/get_zillow_emails`.

The table doesn't show the body_html though. When the user selects a row, it renders the HTML in a container below. 


2. "Create a System Instructions".  
    * 2a. A text area where users can enter a custom System Instruction that will be fed into an LLM prompt. 
    * 2b. A "Add" button. Clicking "Add" will add the text to a 2c.
    * 2c.  "System Instructions" client-side table. Users can create up to 10 system instructions. Only 1 row can be selected at a time.


3. "Generate AI Response" button

5. "Generated Response" Text Area. 


Take some liberties to make the UI feel organized, clean, and intuitive. 

## Follow ups

Add some sort of loading incidator while data is being fetched.  And add a button to get new random emails. 

Cap the height of the Email Content container to be 50% of height (make it scrollable). 

Make the width of the Email Content container much larger vs the Select a Sample Email table. 

Make it more 